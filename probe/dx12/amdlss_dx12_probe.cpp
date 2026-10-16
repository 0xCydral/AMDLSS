/*
 * amdlss_dx12_probe: ReShade add-on for stage 1 of the DX12 spike (odd/tasks/dx12-spike.md).
 *
 * Press F10 in game. The next full frame (present to present) is recorded: every compute dispatch with
 * its shader hash, thread groups and the resources bound to the compute stage, plus render-target
 * changes and draw totals. One capture is appended to amdlss_dx12_probe.log next to this add-on.
 *
 * Bindings are resolved by shadowing the descriptor heaps from update/copy_descriptor_tables, because
 * a D3D12 descriptor table is only a heap offset and ReShade cannot read a descriptor back.
 */
// MinGW declares the __uuidof helper that ReShade's private-data templates need only in guiddef.h.
#include <windows.h>
#include <guiddef.h>
#include <reshade.hpp>

#include <atomic>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

using namespace reshade::api;
using reshade::addon_event;

namespace
{
constexpr uint32_t kHotkey = VK_F10;
// An unbounded range (bindless) would print the whole heap; the first entries are enough to see the pass.
constexpr uint32_t kUnboundedCap = 64;
constexpr uint32_t kMaxHeapDescriptors = 4000000;

// What a view or buffer range pointed at when it was created; shared by every copy of the descriptor.
struct Info
{
	bool buffer_range = false;
	resource_view_type view_type = resource_view_type::unknown;
	format view_format = format::unknown;
	uint64_t offset = 0, range = 0, resource = 0;
	resource_desc desc;
};
struct Entry
{
	bool set = false;
	descriptor_type type = descriptor_type::sampler;
	uint64_t handle = 0;
	std::shared_ptr<const Info> info;
};
struct PipeInfo
{
	uint64_t hash = 0;
	size_t size = 0;
};
struct LayoutParam
{
	pipeline_layout_param_type type = pipeline_layout_param_type::descriptor_table;
	std::vector<descriptor_range> ranges;
};
struct CmdState
{
	uint64_t pipeline = 0, layout = 0;
	std::map<uint32_t, descriptor_table> tables;
	std::map<uint32_t, std::vector<Entry>> pushes;
	std::string last_rt;
	uint32_t epoch = 0;
};

enum class Mode { idle, armed, capturing };

std::mutex g_mutex;
std::unordered_map<uint64_t, PipeInfo> g_pipelines;
std::unordered_map<uint64_t, std::vector<LayoutParam>> g_layouts;
std::unordered_map<uint64_t, std::vector<Entry>> g_heaps;
std::unordered_map<uint64_t, std::shared_ptr<const Info>> g_views;
std::unordered_map<const command_list *, CmdState> g_cmds;

std::atomic<Mode> g_mode { Mode::idle };
std::atomic<uint32_t> g_dispatch_index { 0 }, g_indirect { 0 }, g_draws { 0 }, g_mesh { 0 }, g_rays { 0 };
std::string g_log, g_log_path;
uint32_t g_captures = 0, g_epoch = 0;

std::string str(const char *fmt, ...)
{
	char buf[512];
	va_list args;
	va_start(args, fmt);
	vsnprintf(buf, sizeof(buf), fmt, args);
	va_end(args);
	return buf;
}

uint64_t fnv1a(const void *data, size_t size)
{
	uint64_t h = 14695981039346656037ull;
	for (size_t i = 0; i < size; ++i)
		h = (h ^ static_cast<const uint8_t *>(data)[i]) * 1099511628211ull;
	return h;
}

const char *format_name(format f)
{
	// The values are DXGI_FORMAT, so the names match what a D3D12 capture tool prints.
#define F(n) case format::n: return #n;
	switch (f)
	{
	F(r8_typeless) F(r8_unorm) F(r8_uint) F(r8_snorm) F(r8_sint) F(a8_unorm)
	F(r8g8_typeless) F(r8g8_unorm) F(r8g8_uint) F(r8g8_snorm) F(r8g8_sint)
	F(r8g8b8a8_typeless) F(r8g8b8a8_unorm) F(r8g8b8a8_unorm_srgb) F(r8g8b8a8_uint) F(r8g8b8a8_snorm) F(r8g8b8a8_sint)
	F(b8g8r8a8_typeless) F(b8g8r8a8_unorm) F(b8g8r8a8_unorm_srgb) F(b8g8r8x8_typeless) F(b8g8r8x8_unorm) F(b8g8r8x8_unorm_srgb)
	F(r10g10b10a2_typeless) F(r10g10b10a2_unorm) F(r10g10b10a2_uint) F(r10g10b10a2_xr_bias)
	F(r16_typeless) F(r16_float) F(r16_unorm) F(r16_uint) F(r16_snorm) F(r16_sint)
	F(r16g16_typeless) F(r16g16_float) F(r16g16_unorm) F(r16g16_uint) F(r16g16_snorm) F(r16g16_sint)
	F(r16g16b16a16_typeless) F(r16g16b16a16_float) F(r16g16b16a16_unorm) F(r16g16b16a16_uint) F(r16g16b16a16_snorm) F(r16g16b16a16_sint)
	F(r32_typeless) F(r32_float) F(r32_uint) F(r32_sint)
	F(r32g32_typeless) F(r32g32_float) F(r32g32_uint) F(r32g32_sint)
	F(r32g32b32_typeless) F(r32g32b32_float) F(r32g32b32_uint) F(r32g32b32_sint)
	F(r32g32b32a32_typeless) F(r32g32b32a32_float) F(r32g32b32a32_uint) F(r32g32b32a32_sint)
	F(r9g9b9e5) F(r11g11b10_float) F(b5g6r5_unorm) F(b5g5r5a1_unorm) F(b4g4r4a4_unorm)
	F(d16_unorm) F(d24_unorm_s8_uint) F(d32_float) F(d32_float_s8_uint)
	F(r24_g8_typeless) F(r24_unorm_x8_uint) F(x24_unorm_g8_uint) F(r32_g8_typeless) F(r32_float_x8_uint) F(x32_float_g8_uint)
	F(bc1_typeless) F(bc1_unorm) F(bc1_unorm_srgb) F(bc2_typeless) F(bc2_unorm) F(bc2_unorm_srgb)
	F(bc3_typeless) F(bc3_unorm) F(bc3_unorm_srgb) F(bc4_typeless) F(bc4_unorm) F(bc4_snorm)
	F(bc5_typeless) F(bc5_unorm) F(bc5_snorm) F(bc6h_typeless) F(bc6h_ufloat) F(bc6h_sfloat)
	F(bc7_typeless) F(bc7_unorm) F(bc7_unorm_srgb)
	case format::unknown: return "unknown";
	default: return "fmt?";
	}
#undef F
}

const char *resource_type_name(resource_type t)
{
	switch (t)
	{
	case resource_type::buffer: return "buf";
	case resource_type::texture_1d: return "tex1d";
	case resource_type::texture_2d: return "tex2d";
	case resource_type::texture_3d: return "tex3d";
	case resource_type::surface: return "surface";
	default: return "res?";
	}
}

const char *view_type_name(resource_view_type t)
{
	switch (t)
	{
	case resource_view_type::buffer: return "buffer";
	case resource_view_type::texture_1d: return "1d";
	case resource_view_type::texture_1d_array: return "1d_array";
	case resource_view_type::texture_2d: return "2d";
	case resource_view_type::texture_2d_array: return "2d_array";
	case resource_view_type::texture_2d_multisample: return "2d_ms";
	case resource_view_type::texture_2d_multisample_array: return "2d_ms_array";
	case resource_view_type::texture_3d: return "3d";
	case resource_view_type::texture_cube: return "cube";
	case resource_view_type::texture_cube_array: return "cube_array";
	case resource_view_type::acceleration_structure: return "accel";
	default: return "view?";
	}
}

std::string usage_names(resource_usage usage)
{
	static const struct { resource_usage mask; const char *name; } flags[] = {
		{ resource_usage::render_target, "RTV" }, { resource_usage::depth_stencil, "DSV" },
		{ resource_usage::shader_resource, "SRV" }, { resource_usage::unordered_access, "UAV" },
		{ resource_usage::constant_buffer, "CBV" }, { resource_usage::indirect_argument, "INDIRECT" },
		{ resource_usage::copy_source, "COPY_SRC" }, { resource_usage::copy_dest, "COPY_DST" },
	};
	std::string out;
	for (const auto &f : flags)
		if ((usage & f.mask) == f.mask)
			out += (out.empty() ? "" : "|") + std::string(f.name);
	return out.empty() ? "-" : out;
}

std::string resource_text(const resource_desc &d)
{
	if (d.type == resource_type::buffer)
		return str("buf size=%llu stride=%u use=%s", (unsigned long long)d.buffer.size, d.buffer.structured.stride, usage_names(d.usage).c_str());
	return str("%s %ux%ux%u m%u s%u %s use=%s", resource_type_name(d.type), d.texture.width, d.texture.height,
		d.texture.depth_or_layers, d.texture.levels, d.texture.samples, format_name(d.texture.format), usage_names(d.usage).c_str());
}

std::string view_text(device *dev, const Entry &e)
{
	if (e.info != nullptr)
	{
		if (e.info->buffer_range)
			return str("r=%llx off=%llu range=%lld %s", (unsigned long long)e.info->resource, (unsigned long long)e.info->offset,
				(long long)e.info->range, e.info->resource ? resource_text(e.info->desc).c_str() : "null");
		return str("r=%llx %s view=%s:%s", (unsigned long long)e.info->resource, resource_text(e.info->desc).c_str(),
			view_type_name(e.info->view_type), format_name(e.info->view_format));
	}
	if (e.type == descriptor_type::sampler)
		return str("sampler %llx", (unsigned long long)e.handle);
	if (e.handle == 0)
		return "null";
	// No creation snapshot (view made before the add-on loaded): ask ReShade, the view may be stale.
	const resource res = dev->get_resource_from_view({ e.handle });
	if (res.handle == 0)
		return str("view=%llx unresolved", (unsigned long long)e.handle);
	const resource_view_desc vd = dev->get_resource_view_desc({ e.handle });
	return str("r=%llx %s view=%s:%s (live lookup)", (unsigned long long)res.handle, resource_text(dev->get_resource_desc(res)).c_str(),
		view_type_name(vd.type), format_name(vd.format));
}

const char *kind(descriptor_type t)
{
	switch (t)
	{
	case descriptor_type::sampler: return "SMP";
	case descriptor_type::sampler_with_resource_view: return "SMP+SRV";
	case descriptor_type::shader_resource_view:
	case descriptor_type::buffer_shader_resource_view:
	case descriptor_type::acceleration_structure: return "SRV";
	case descriptor_type::unordered_access_view:
	case descriptor_type::buffer_unordered_access_view:
	case descriptor_type::shader_storage_buffer:
	case descriptor_type::shader_storage_buffer_with_dynamic_offset: return "UAV";
	default: return "CBV";
	}
}

Entry make_entry(device *dev, descriptor_type type, const void *descriptors, uint32_t k)
{
	Entry e;
	e.set = true;
	e.type = type;
	switch (type)
	{
	case descriptor_type::sampler:
		e.handle = static_cast<const reshade::api::sampler *>(descriptors)[k].handle;
		break;
	case descriptor_type::sampler_with_resource_view:
		e.handle = static_cast<const sampler_with_resource_view *>(descriptors)[k].view.handle;
		break;
	case descriptor_type::constant_buffer:
	case descriptor_type::constant_buffer_with_dynamic_offset:
	case descriptor_type::shader_storage_buffer:
	case descriptor_type::shader_storage_buffer_with_dynamic_offset:
	{
		const buffer_range &r = static_cast<const buffer_range *>(descriptors)[k];
		auto info = std::make_shared<Info>();
		info->buffer_range = true;
		info->resource = r.buffer.handle;
		info->offset = r.offset;
		info->range = r.size;
		if (r.buffer.handle != 0)
			info->desc = dev->get_resource_desc(r.buffer);
		e.info = std::move(info);
		break;
	}
	default:
		e.handle = static_cast<const resource_view *>(descriptors)[k].handle;
		break;
	}
	return e;
}

// The creation snapshot lands after update_descriptor_tables, so views are bound to their info lazily.
void resolve(Entry &e)
{
	if (e.info != nullptr || e.handle == 0 || e.type == descriptor_type::sampler)
		return;
	if (const auto it = g_views.find(e.handle); it != g_views.end())
		e.info = it->second;
}

bool heap_offset(device *dev, descriptor_table table, uint32_t binding, uint32_t array_offset, uint64_t &heap, uint32_t &offset)
{
	descriptor_heap h = { 0 };
	dev->get_descriptor_heap_offset(table, binding, array_offset, &h, &offset);
	heap = h.handle;
	return heap != 0;
}

void append(const std::string &line)
{
	g_log += line;
	g_log += '\n';
}

std::string slot_label(const descriptor_range &r, descriptor_type type, uint32_t j)
{
	const char *k = kind(r.type);
	const char reg = k[0] == 'S' && k[1] == 'M' ? 's' : k[0] == 'S' ? 't' : k[0] == 'U' ? 'u' : 'b';
	const std::string space = r.dx_register_space ? str(",sp%u", r.dx_register_space) : "";
	return str("%s %c%u%s", kind(type), reg, r.dx_register_index + j, space.c_str());
}

std::string describe_bindings(device *dev, CmdState &st)
{
	const auto layout = g_layouts.find(st.layout);
	if (layout == g_layouts.end())
		return "no-layout";
	std::string out;
	for (const auto &[param, table] : st.tables)
	{
		if (param >= layout->second.size())
			continue;
		for (const descriptor_range &r : layout->second[param].ranges)
		{
			if ((r.visibility & shader_stage::compute) == static_cast<shader_stage>(0))
				continue;
			uint64_t heap;
			uint32_t base;
			if (!heap_offset(dev, table, r.binding, 0, heap, base))
				continue;
			const auto h = g_heaps.find(heap);
			if (h == g_heaps.end())
				continue;
			const uint32_t n = r.count == UINT32_MAX ? kUnboundedCap : r.count;
			for (uint32_t j = 0; j < n && base + j < h->second.size(); ++j)
			{
				Entry &e = h->second[base + j];
				if (!e.set)
					continue;
				resolve(e);
				out += str(" [p%u %s %s]", param, slot_label(r, e.type, j).c_str(), view_text(dev, e).c_str());
			}
		}
	}
	for (const auto &[param, entries] : st.pushes)
	{
		const descriptor_range *r = param < layout->second.size() && !layout->second[param].ranges.empty() ? &layout->second[param].ranges[0] : nullptr;
		for (uint32_t k = 0; k < entries.size(); ++k)
		{
			Entry e = entries[k];
			resolve(e);
			out += str(" [p%u root %s%u %s]", param, kind(e.type), r ? r->dx_register_index + k : k, view_text(dev, e).c_str());
		}
	}
	return out.empty() ? " (nothing bound)" : out;
}

void log_dispatch(command_list *cmd_list, const std::string &groups)
{
	if (g_mode != Mode::capturing)
		return;
	const uint32_t index = g_dispatch_index++;
	std::lock_guard<std::mutex> lock(g_mutex);
	CmdState &st = g_cmds[cmd_list];
	const auto pipe = g_pipelines.find(st.pipeline);
	const std::string cs = pipe == g_pipelines.end() ? "cs=?" : str("cs=%016llx/%llu", (unsigned long long)pipe->second.hash, (unsigned long long)pipe->second.size);
	append(str("D %u cl=%p %s groups=%s |", index, (const void *)cmd_list, cs.c_str(), groups.c_str()) + describe_bindings(cmd_list->get_device(), st));
}

// Compute bindings are tied to the root signature; a new layout drops what was bound before.
CmdState &compute_state(command_list *cmd_list, pipeline_layout layout)
{
	CmdState &st = g_cmds[cmd_list];
	if (st.layout != layout.handle)
	{
		st.layout = layout.handle;
		st.tables.clear();
		st.pushes.clear();
	}
	return st;
}

void on_init_pipeline(device *, pipeline_layout, uint32_t count, const pipeline_subobject *subobjects, pipeline pipe)
{
	for (uint32_t i = 0; i < count; ++i)
	{
		if (subobjects[i].type != pipeline_subobject_type::compute_shader || subobjects[i].count == 0)
			continue;
		const auto *shader = static_cast<const shader_desc *>(subobjects[i].data);
		PipeInfo info;
		if (shader->code != nullptr)
		{
			info.hash = fnv1a(shader->code, shader->code_size);
			info.size = shader->code_size;
		}
		std::lock_guard<std::mutex> lock(g_mutex);
		g_pipelines[pipe.handle] = info;
	}
}
void on_destroy_pipeline(device *, pipeline pipe)
{
	std::lock_guard<std::mutex> lock(g_mutex);
	g_pipelines.erase(pipe.handle);
}

void on_init_pipeline_layout(device *, uint32_t count, const pipeline_layout_param *params, pipeline_layout layout)
{
	std::vector<LayoutParam> out(count);
	for (uint32_t i = 0; i < count; ++i)
	{
		out[i].type = params[i].type;
		switch (params[i].type)
		{
		case pipeline_layout_param_type::push_descriptors:
			out[i].ranges.push_back(params[i].push_descriptors);
			break;
		case pipeline_layout_param_type::descriptor_table:
		case pipeline_layout_param_type::push_descriptors_with_ranges:
			out[i].ranges.assign(params[i].descriptor_table.ranges, params[i].descriptor_table.ranges + params[i].descriptor_table.count);
			break;
		case pipeline_layout_param_type::descriptor_table_with_flags:
		case pipeline_layout_param_type::push_descriptors_with_ranges_and_flags:
			for (uint32_t k = 0; k < params[i].descriptor_table_with_flags.count; ++k)
				out[i].ranges.push_back(params[i].descriptor_table_with_flags.ranges[k]);
			break;
		default:
			break;
		}
	}
	std::lock_guard<std::mutex> lock(g_mutex);
	g_layouts[layout.handle] = std::move(out);
}
void on_destroy_pipeline_layout(device *, pipeline_layout layout)
{
	std::lock_guard<std::mutex> lock(g_mutex);
	g_layouts.erase(layout.handle);
}

void on_init_resource_view(device *dev, resource res, resource_usage, const resource_view_desc &desc, resource_view view)
{
	auto info = std::make_shared<Info>();
	info->view_type = desc.type;
	info->view_format = desc.format;
	info->resource = res.handle;
	if (res.handle != 0)
		info->desc = dev->get_resource_desc(res);
	std::lock_guard<std::mutex> lock(g_mutex);
	g_views[view.handle] = std::move(info);
}
void on_destroy_resource_view(device *, resource_view view)
{
	std::lock_guard<std::mutex> lock(g_mutex);
	g_views.erase(view.handle);
}

bool on_update_descriptor_tables(device *dev, uint32_t count, const descriptor_table_update *updates)
{
	std::lock_guard<std::mutex> lock(g_mutex);
	for (uint32_t i = 0; i < count; ++i)
	{
		const descriptor_table_update &u = updates[i];
		uint64_t heap;
		uint32_t offset;
		if (!heap_offset(dev, u.table, u.binding, u.array_offset, heap, offset) || offset + u.count > kMaxHeapDescriptors)
			continue;
		std::vector<Entry> &slots = g_heaps[heap];
		if (slots.size() < offset + u.count)
			slots.resize(offset + u.count);
		for (uint32_t k = 0; k < u.count; ++k)
			slots[offset + k] = make_entry(dev, u.type, u.descriptors, k);
	}
	return false;
}

bool on_copy_descriptor_tables(device *dev, uint32_t count, const descriptor_table_copy *copies)
{
	std::lock_guard<std::mutex> lock(g_mutex);
	for (uint32_t i = 0; i < count; ++i)
	{
		const descriptor_table_copy &c = copies[i];
		uint64_t src_heap, dst_heap;
		uint32_t src_off, dst_off;
		if (!heap_offset(dev, c.source_table, c.source_binding, c.source_array_offset, src_heap, src_off) ||
			!heap_offset(dev, c.dest_table, c.dest_binding, c.dest_array_offset, dst_heap, dst_off) ||
			dst_off + c.count > kMaxHeapDescriptors)
			continue;
		std::vector<Entry> &src = g_heaps[src_heap], &dst = g_heaps[dst_heap];
		if (dst.size() < dst_off + c.count)
			dst.resize(dst_off + c.count);
		for (uint32_t k = 0; k < c.count; ++k)
		{
			Entry e;
			if (src_off + k < src.size())
			{
				resolve(src[src_off + k]);
				e = src[src_off + k];
			}
			dst[dst_off + k] = std::move(e);
		}
	}
	return false;
}

void on_reset_command_list(command_list *cmd_list)
{
	std::lock_guard<std::mutex> lock(g_mutex);
	g_cmds.erase(cmd_list);
}

void on_bind_pipeline(command_list *cmd_list, pipeline_stage stages, pipeline pipe)
{
	if ((stages & pipeline_stage::compute_shader) == static_cast<pipeline_stage>(0))
		return;
	std::lock_guard<std::mutex> lock(g_mutex);
	g_cmds[cmd_list].pipeline = pipe.handle;
}

void on_push_descriptors(command_list *cmd_list, shader_stage stages, pipeline_layout layout, uint32_t param, const descriptor_table_update &update)
{
	if ((stages & shader_stage::compute) == static_cast<shader_stage>(0))
		return;
	std::vector<Entry> entries;
	for (uint32_t k = 0; k < update.count; ++k)
		entries.push_back(make_entry(cmd_list->get_device(), update.type, update.descriptors, k));
	std::lock_guard<std::mutex> lock(g_mutex);
	compute_state(cmd_list, layout).pushes[param] = std::move(entries);
}

void on_bind_descriptor_tables(command_list *cmd_list, shader_stage stages, pipeline_layout layout, uint32_t first, uint32_t count, const descriptor_table *tables, uint32_t, const uint32_t *)
{
	if ((stages & shader_stage::compute) == static_cast<shader_stage>(0))
		return;
	std::lock_guard<std::mutex> lock(g_mutex);
	CmdState &st = compute_state(cmd_list, layout);
	for (uint32_t i = 0; i < count; ++i)
		st.tables[first + i] = tables[i];
}

std::string view_brief(resource_view view)
{
	if (view.handle == 0)
		return "-";
	const auto it = g_views.find(view.handle);
	if (it != g_views.end() && it->second->desc.type != resource_type::unknown && it->second->desc.type != resource_type::buffer)
		return str("%ux%u:%s", it->second->desc.texture.width, it->second->desc.texture.height, format_name(it->second->view_format));
	return str("view=%llx", (unsigned long long)view.handle);
}

void on_bind_render_targets(command_list *cmd_list, uint32_t count, const resource_view *rtvs, resource_view dsv)
{
	if (g_mode != Mode::capturing)
		return;
	std::lock_guard<std::mutex> lock(g_mutex);
	CmdState &st = g_cmds[cmd_list];
	if (st.epoch != g_epoch)
	{
		st.epoch = g_epoch;
		st.last_rt.clear();
	}
	std::string text;
	for (uint32_t i = 0; i < count; ++i)
		text += " rt" + std::to_string(i) + "=" + view_brief(rtvs[i]);
	text += " ds=" + view_brief(dsv);
	if (text == st.last_rt)
		return;
	st.last_rt = text;
	append(str("RT cl=%p at_dispatch=%u draws=%u", (const void *)cmd_list, g_dispatch_index.load(), g_draws.load()) + text);
}

bool on_dispatch(command_list *cmd_list, uint32_t x, uint32_t y, uint32_t z)
{
	log_dispatch(cmd_list, str("%ux%ux%u", x, y, z));
	return false;
}
bool on_dispatch_mesh(command_list *, uint32_t, uint32_t, uint32_t)
{
	if (g_mode == Mode::capturing)
		++g_mesh;
	return false;
}
bool on_dispatch_rays(command_list *, resource, uint64_t, uint64_t, resource, uint64_t, uint64_t, uint64_t, resource, uint64_t, uint64_t, uint64_t, resource, uint64_t, uint64_t, uint64_t, uint32_t, uint32_t, uint32_t)
{
	if (g_mode == Mode::capturing)
		++g_rays;
	return false;
}
bool on_draw(command_list *, uint32_t, uint32_t, uint32_t, uint32_t)
{
	if (g_mode == Mode::capturing)
		++g_draws;
	return false;
}
bool on_draw_indexed(command_list *, uint32_t, uint32_t, uint32_t, int32_t, uint32_t)
{
	if (g_mode == Mode::capturing)
		++g_draws;
	return false;
}
// ExecuteIndirect: UE5 issues its GPU-driven passes this way, so the group counts are unknown here.
bool on_indirect(command_list *cmd_list, indirect_command type, resource buffer, uint64_t offset, uint32_t draw_count, uint32_t)
{
	if (g_mode != Mode::capturing)
		return false;
	switch (type)
	{
	case indirect_command::dispatch:
		++g_indirect;
		log_dispatch(cmd_list, str("indirect(buf=%llx,off=%llu,n=%u)", (unsigned long long)buffer.handle, (unsigned long long)offset, draw_count));
		break;
	case indirect_command::dispatch_mesh: ++g_mesh; break;
	case indirect_command::dispatch_rays: ++g_rays; break;
	default: ++g_draws; break;
	}
	return false;
}

void begin_capture(swapchain *swap)
{
	device *dev = swap->get_device();
	const resource_desc bb = dev->get_resource_desc(swap->get_current_back_buffer());
	std::lock_guard<std::mutex> lock(g_mutex);
	g_log.clear();
	++g_captures;
	++g_epoch;
	g_dispatch_index = g_indirect = g_draws = g_mesh = g_rays = 0;
	append(str("=== capture #%u backbuffer=%ux%u %s ===", g_captures, bb.texture.width, bb.texture.height, format_name(bb.texture.format)));
	g_mode = Mode::capturing;
}

void finish_capture()
{
	g_mode = Mode::idle;
	std::lock_guard<std::mutex> lock(g_mutex);
	append(str("=== end capture #%u dispatches=%u (indirect=%u) draws=%u dispatch_mesh=%u dispatch_rays=%u ===",
		g_captures, g_dispatch_index.load(), g_indirect.load(), g_draws.load(), g_mesh.load(), g_rays.load()));
	if (FILE *f = fopen(g_log_path.c_str(), "ab"))
	{
		fwrite(g_log.data(), 1, g_log.size(), f);
		fclose(f);
	}
	else
		reshade::log::message(reshade::log::level::error, ("amdlss_dx12_probe: cannot write " + g_log_path).c_str());
}

// Frame boundary: armed -> record the frame that starts now -> write it at the next present.
void on_present(command_queue *, swapchain *swap, const rect *, const rect *, uint32_t, const rect *)
{
	if (g_mode == Mode::capturing)
		finish_capture();
	else if (g_mode == Mode::armed)
		begin_capture(swap);
}

void on_reshade_present(effect_runtime *runtime)
{
	static bool was_down = false;
	const bool down = runtime->is_key_pressed(kHotkey) || (GetAsyncKeyState(kHotkey) & 0x8000) != 0;
	if (down && !was_down && g_mode == Mode::idle)
		g_mode = Mode::armed;
	was_down = down;
}
} // namespace

extern "C"
{
__declspec(dllexport) const char *NAME = "AMDLSS DX12 probe";
__declspec(dllexport) const char *DESCRIPTION = "Press F10 to log one frame of compute dispatches and their bound resources to amdlss_dx12_probe.log.";
}

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID)
{
	switch (reason)
	{
	case DLL_PROCESS_ATTACH:
	{
		if (!reshade::register_addon(module))
			return FALSE;

		char path[MAX_PATH];
		const DWORD n = GetModuleFileNameA(module, path, MAX_PATH);
		g_log_path.assign(path, n);
		g_log_path.erase(g_log_path.find_last_of("\\/") + 1);
		g_log_path += "amdlss_dx12_probe.log";

		reshade::register_event<addon_event::init_pipeline>(on_init_pipeline);
		reshade::register_event<addon_event::destroy_pipeline>(on_destroy_pipeline);
		reshade::register_event<addon_event::init_pipeline_layout>(on_init_pipeline_layout);
		reshade::register_event<addon_event::destroy_pipeline_layout>(on_destroy_pipeline_layout);
		reshade::register_event<addon_event::init_resource_view>(on_init_resource_view);
		reshade::register_event<addon_event::destroy_resource_view>(on_destroy_resource_view);
		reshade::register_event<addon_event::update_descriptor_tables>(on_update_descriptor_tables);
		reshade::register_event<addon_event::copy_descriptor_tables>(on_copy_descriptor_tables);
		reshade::register_event<addon_event::reset_command_list>(on_reset_command_list);
		reshade::register_event<addon_event::destroy_command_list>(on_reset_command_list);
		reshade::register_event<addon_event::bind_pipeline>(on_bind_pipeline);
		reshade::register_event<addon_event::push_descriptors>(on_push_descriptors);
		reshade::register_event<addon_event::bind_descriptor_tables>(on_bind_descriptor_tables);
		reshade::register_event<addon_event::bind_render_targets_and_depth_stencil>(on_bind_render_targets);
		reshade::register_event<addon_event::dispatch>(on_dispatch);
		reshade::register_event<addon_event::dispatch_mesh>(on_dispatch_mesh);
		reshade::register_event<addon_event::dispatch_rays>(on_dispatch_rays);
		reshade::register_event<addon_event::draw>(on_draw);
		reshade::register_event<addon_event::draw_indexed>(on_draw_indexed);
		reshade::register_event<addon_event::draw_or_dispatch_indirect>(on_indirect);
		reshade::register_event<addon_event::present>(on_present);
		reshade::register_event<addon_event::reshade_present>(on_reshade_present);
		break;
	}
	case DLL_PROCESS_DETACH:
		reshade::unregister_addon(module);
		break;
	}
	return TRUE;
}
