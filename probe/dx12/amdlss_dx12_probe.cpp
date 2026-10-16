/*
 * amdlss_dx12_probe: ReShade add-on for stage 1 of the DX12 spike (odd/tasks/dx12-spike.md).
 *
 * Press F10 in game. The next full frame (present to present) is recorded: every compute dispatch with
 * its shader hash, thread groups and the resources bound to the compute stage, plus render-target
 * changes and draw totals. One capture is appended to amdlss_dx12_probe.log next to this add-on.
 *
 * Bindings are resolved by shadowing the descriptor heaps from update/copy_descriptor_tables, because
 * a D3D12 descriptor table is only a heap offset and ReShade cannot read a descriptor back.
 *
 * Press F11 (stage 2) to copy the textures around the upscale dispatch (shader hash kUpscaleHash) to dump/ as .dds files:
 * the inputs just before the dispatch and the outputs right after it, on the same command list.
 */
#include <reshade.hpp>

#include <algorithm>
#include <atomic>
#include <cstring>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
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
	std::map<uint32_t, std::vector<uint32_t>> consts; // root constants per root parameter, in 32-bit values
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
		st.consts.clear();
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

// ---- Stage 2: F11 dumps the textures around the upscale dispatch to dump/*.dds ----------------------------

// Compute shader found in stage 1 ("Stage 1 result" in odd/tasks/dx12-spike.md): one thread per output pixel.
constexpr uint64_t kUpscaleHash = 0x1ff10c3ce95d3bcbull;
constexpr uint32_t kDumpHotkey = VK_F11;
// The shader's own descriptor table; root param 0 lists resources it never reads.
constexpr uint32_t kUpscaleRootParam = 1;
// u0-u2 are the pass outputs, the other UAVs are inputs or scratch and are only dumped before the dispatch.
constexpr uint32_t kMaxPostUavRegister = 2;
// Guesses from how UE5 leaves resources (compute reads use NON_PIXEL_SHADER_RESOURCE). vkd3d-proton tolerates a wrong
// "before" state; native D3D12 may not. The depth state is the weakest guess: that depth is not bound to the dispatch.
constexpr resource_usage kSrvState = resource_usage::shader_resource_non_pixel;
constexpr resource_usage kDepthState = resource_usage::shader_resource_non_pixel;
constexpr size_t kDsvHistory = 32;
// If the recorded command list is never submitted (reset, dropped), stop waiting after this many presents.
constexpr uint32_t kGiveUpPresents = 600;

struct DumpItem
{
	std::string phase, slots, view;
	resource res = { 0 };
	resource_desc desc;
	resource_usage state = resource_usage::undefined;
	format fmt = format::unknown; // what the bytes in the file are
	uint32_t texel = 0, width = 0, height = 0, row_length = 0, stride = 0;
	bool uav = false;
	uint32_t reg = 0;
	resource readback = { 0 };
	uint64_t size = 0;
};
enum class DumpMode { idle, armed, recording, waiting };
struct DumpRun
{
	command_list *cmd_list = nullptr;
	device *dev = nullptr;
	command_queue *queue = nullptr;
	uint32_t number = 0, started = 0, submitted_at = 0;
	bool submitted = false;
	std::vector<DumpItem> pre, post;
};

std::atomic<DumpMode> g_dump_mode { DumpMode::idle };
std::atomic<uint32_t> g_presents { 0 };
std::atomic<uint64_t> g_last_dsv { 0 };
DumpRun g_run;
uint32_t g_dump_count = 0;
std::vector<uint64_t> g_dsv_history;
std::string g_dir; // add-on directory, with trailing separator

void note(const std::string &line)
{
	reshade::log::message(reshade::log::level::info, ("amdlss_dx12_probe: " + line).c_str());
	if (FILE *f = fopen(g_log_path.c_str(), "ab"))
	{
		fputs(("DUMP " + line + "\n").c_str(), f);
		fclose(f);
	}
}

void dump_release()
{
	for (std::vector<DumpItem> *items : { &g_run.pre, &g_run.post })
		for (DumpItem &it : *items)
			if (it.readback.handle != 0 && g_run.dev != nullptr)
				g_run.dev->destroy_resource(it.readback);
	g_run = DumpRun();
	g_dump_mode = DumpMode::idle;
}

// Plane 0 of a depth resource is what a copy returns, so a depth-stencil texture is written as its depth-only twin.
bool pick_format(const resource_desc &d, format view_fmt, DumpItem &it)
{
	const format rf = d.texture.format;
	if ((d.usage & resource_usage::depth_stencil) != static_cast<resource_usage>(0))
	{
		switch (rf)
		{
		case format::r32_g8_typeless: case format::d32_float_s8_uint: case format::r32_float_x8_uint:
		case format::x32_float_g8_uint: case format::r32_typeless: case format::d32_float:
			it.fmt = format::r32_float; it.texel = 4; return true;
		case format::r24_g8_typeless: case format::d24_unorm_s8_uint: case format::r24_unorm_x8_uint: case format::x24_unorm_g8_uint:
			it.fmt = format::r24_unorm_x8_uint; it.texel = 4; return true;
		case format::r16_typeless: case format::d16_unorm:
			it.fmt = format::r16_unorm; it.texel = 2; return true;
		default:
			return false;
		}
	}
	const bool view_ok = view_fmt != format::unknown && !format_is_typeless(view_fmt) && format_row_pitch(view_fmt, 1) == format_row_pitch(rf, 1);
	const format f = view_ok ? view_fmt : format_to_default_typed(rf);
	if (f == format::unknown || format_is_typeless(f) || format_is_block_compressed(f) || format_row_pitch(f, 1) == 0)
		return false;
	it.fmt = f;
	it.texel = format_row_pitch(f, 1);
	return true;
}

bool make_item(const Info &info, const char *phase, const std::string &slot, bool uav, uint32_t reg, DumpItem &it)
{
	const resource_desc &d = info.desc;
	if (info.resource == 0 || d.type != resource_type::texture_2d || d.texture.samples > 1 || d.texture.width == 0 || d.texture.height == 0)
		return false;
	it.phase = phase;
	it.slots = slot;
	it.view = view_type_name(info.view_type);
	it.res = { info.resource };
	it.desc = d;
	it.uav = uav;
	it.reg = reg;
	it.state = uav ? resource_usage::unordered_access : kSrvState;
	it.width = d.texture.width;
	it.height = d.texture.height;
	if (!pick_format(d, info.view_format, it))
		return false;
	// Whole rows padded to 256 texels keep the pitch 256-byte aligned for every texel size, as D3D12 requires.
	it.row_length = (it.width + 255) & ~255u;
	// Pitch from the copied texel size: a depth copy holds plane 0 only, half of an r32_g8 texel.
	it.stride = it.texel * it.row_length;
	it.size = static_cast<uint64_t>(it.stride) * it.height;
	return it.stride != 0;
}

void add_item(std::vector<DumpItem> &items, DumpItem &&it)
{
	for (DumpItem &other : items)
		if (other.res.handle == it.res.handle)
		{
			other.slots += "+" + it.slots;
			return;
		}
	items.push_back(std::move(it));
}

void collect_upscale_bindings(device *dev, CmdState &st, std::vector<DumpItem> &out, bool quiet = false)
{
	const auto layout = g_layouts.find(st.layout);
	const auto table = st.tables.find(kUpscaleRootParam);
	if (layout == g_layouts.end() || table == st.tables.end() || kUpscaleRootParam >= layout->second.size())
		return;
	for (const descriptor_range &r : layout->second[kUpscaleRootParam].ranges)
	{
		const bool uav = r.type == descriptor_type::unordered_access_view;
		if (!uav && r.type != descriptor_type::shader_resource_view)
			continue;
		uint64_t heap;
		uint32_t base;
		if (!heap_offset(dev, table->second, r.binding, 0, heap, base))
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
			const std::string slot = std::string(1, uav ? 'u' : 't') + std::to_string(r.dx_register_index + j);
			DumpItem it;
			if (e.info == nullptr || e.info->buffer_range || !make_item(*e.info, "pre", slot, uav, r.dx_register_index + j, it))
			{
				if (e.handle != 0 && !quiet)
					note("skip " + slot + ": not a plain 2D texture or format not supported");
				continue;
			}
			add_item(out, std::move(it));
		}
	}
}

// The most recent depth buffer bound this session that has the render-size dimensions of the SRV inputs.
void add_render_size_depth(std::vector<DumpItem> &items)
{
	std::map<std::pair<uint32_t, uint32_t>, int> sizes;
	// Count every binding: UE declares this table as UAV ranges even for the slots it only reads.
	for (const DumpItem &it : items)
		++sizes[{ it.width, it.height }];
	std::pair<uint32_t, uint32_t> best { 0, 0 };
	int best_count = 0;
	for (const auto &[size, count] : sizes)
		if (count > best_count)
		{
			best = size;
			best_count = count;
		}
	for (auto h = g_dsv_history.rbegin(); h != g_dsv_history.rend(); ++h)
	{
		const auto v = g_views.find(*h);
		if (v == g_views.end() || v->second->desc.type != resource_type::texture_2d ||
			v->second->desc.texture.width != best.first || v->second->desc.texture.height != best.second)
			continue;
		DumpItem it;
		if (make_item(*v->second, "pre", "depth", false, 0, it))
		{
			it.state = kDepthState;
			add_item(items, std::move(it));
			return;
		}
	}
	note(str("no depth buffer of render size %ux%u seen", best.first, best.second));
}

// Drops items whose readback buffer cannot be created, so one bad texture never costs the rest.
void allocate_readbacks(device *dev, std::vector<DumpItem> &items)
{
	for (size_t i = 0; i < items.size();)
	{
		DumpItem &it = items[i];
		if (dev->create_resource(resource_desc(it.size, memory_heap::readback, resource_usage::copy_dest), nullptr, resource_usage::copy_dest, &it.readback) && it.readback.handle != 0)
			++i;
		else
		{
			note("skip " + it.slots + ": cannot create readback buffer");
			items.erase(items.begin() + i);
		}
	}
}

void record_copies(command_list *cmd_list, const std::vector<DumpItem> &items)
{
	if (items.empty())
		return;
	std::vector<resource> res;
	std::vector<resource_usage> from, to;
	for (const DumpItem &it : items)
	{
		res.push_back(it.res);
		from.push_back(it.state);
		to.push_back(resource_usage::copy_source);
	}
	const uint32_t n = static_cast<uint32_t>(res.size());
	cmd_list->barrier(n, res.data(), from.data(), to.data());
	for (const DumpItem &it : items)
		cmd_list->copy_texture_to_buffer(it.res, 0, nullptr, it.readback, 0, it.row_length, 0);
	cmd_list->barrier(n, res.data(), to.data(), from.data());
}

void dump_begin(command_list *cmd_list, CmdState &st)
{
	device *dev = cmd_list->get_device();
	std::vector<DumpItem> pre;
	collect_upscale_bindings(dev, st, pre);
	if (pre.empty())
	{
		note("target dispatch has nothing bound at root param 1; disarmed");
		g_dump_mode = DumpMode::idle;
		return;
	}
	add_render_size_depth(pre);
	allocate_readbacks(dev, pre);
	record_copies(cmd_list, pre);

	g_run = DumpRun();
	g_run.cmd_list = cmd_list;
	g_run.dev = dev;
	g_run.number = ++g_dump_count;
	g_run.started = g_presents;
	for (const DumpItem &it : pre)
		if (it.uav && it.reg <= kMaxPostUavRegister)
		{
			DumpItem post = it;
			post.phase = "post";
			post.readback = { 0 };
			g_run.post.push_back(std::move(post));
		}
	g_run.pre = std::move(pre);
	g_dump_mode = DumpMode::recording;
	note(str("dump #%u: recorded %zu pre copies on cl=%p, waiting for the next event on that list", g_run.number, g_run.pre.size(), (const void *)cmd_list));
}

// ReShade only offers barrier "after" ResourceBarrier, so a barrier on the outputs reports the state they were just
// moved to; that state is what the copy must start from.
void dump_post(command_list *cmd_list, uint32_t count, const resource *res, const resource_usage *new_states)
{
	for (uint32_t i = 0; i < count; ++i)
		for (DumpItem &it : g_run.post)
			if (it.res.handle == res[i].handle && new_states[i] != resource_usage::undefined)
				it.state = new_states[i];
	allocate_readbacks(g_run.dev, g_run.post);
	record_copies(cmd_list, g_run.post);
	g_dump_mode = DumpMode::waiting;
	note(str("dump #%u: recorded %zu post copies", g_run.number, g_run.post.size()));
}

// Called before dispatch/draw/indirect and after barrier/close. The first call after the target dispatch on its
// own command list records the post copies: the outputs have been written and UE has not reused them yet.
void dump_event(command_list *cmd_list, bool is_dispatch, uint32_t count = 0, const resource *res = nullptr, const resource_usage *new_states = nullptr)
{
	const DumpMode m = g_dump_mode;
	if (m != DumpMode::armed && m != DumpMode::recording)
		return;
	std::lock_guard<std::mutex> lock(g_mutex);
	if (g_dump_mode == DumpMode::armed)
	{
		if (!is_dispatch)
			return;
		CmdState &st = g_cmds[cmd_list];
		const auto pipe = g_pipelines.find(st.pipeline);
		if (pipe != g_pipelines.end() && pipe->second.hash == kUpscaleHash)
			dump_begin(cmd_list, st);
	}
	else if (g_dump_mode == DumpMode::recording && g_run.cmd_list == cmd_list)
		dump_post(cmd_list, count, res, new_states);
}

bool write_dds(const std::string &path, const DumpItem &it, const uint8_t *data)
{
	FILE *f = fopen(path.c_str(), "wb");
	if (f == nullptr)
		return false;
	const uint32_t header[37] = {
		0x20534444, 124, 0x100F, it.height, it.width, it.width * it.texel, 0, 1,
		0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, // reserved1[11]
		32, 0x4, 0x30315844, 0, 0, 0, 0, 0, // pixel format: fourCC 'DX10'
		0x1000, 0, 0, 0, 0, // caps, caps2-4, reserved2
		static_cast<uint32_t>(it.fmt), 3, 0, 1, 0, // DX10 header: DXGI format, TEXTURE2D, misc, array size, misc2
	};
	bool ok = fwrite(header, sizeof(header), 1, f) == 1;
	for (uint32_t y = 0; ok && y < it.height; ++y)
		ok = fwrite(data + static_cast<size_t>(y) * it.stride, 1, static_cast<size_t>(it.width) * it.texel, f) == static_cast<size_t>(it.width) * it.texel;
	return fclose(f) == 0 && ok;
}

void dump_write()
{
	CreateDirectoryA((g_dir + "dump").c_str(), nullptr);
	const std::string base = str("%sdump\\%03u_", g_dir.c_str(), g_run.number);
	std::string index = "file | slots | view | format | size | resource\n";
	for (std::vector<DumpItem> *items : { &g_run.pre, &g_run.post })
		for (DumpItem &it : *items)
		{
			void *data = nullptr;
			if (!g_run.dev->map_buffer_region(it.readback, 0, it.size, map_access::read_only, &data) || data == nullptr)
			{
				note("skip " + it.slots + ": cannot map readback buffer");
				continue;
			}
			const std::string name = str("%s_%s_%ux%u_%s_%llx.dds", it.phase.c_str(), it.slots.substr(0, it.slots.find('+')).c_str(),
				it.width, it.height, format_name(it.fmt), (unsigned long long)it.res.handle);
			const bool ok = write_dds(base + name, it, static_cast<const uint8_t *>(data));
			g_run.dev->unmap_buffer_region(it.readback);
			if (!ok)
			{
				note("skip " + it.slots + ": cannot write " + name);
				continue;
			}
			index += str("%03u_%s | %s | %s | %s | %ux%u | %llx\n", g_run.number, name.c_str(), it.slots.c_str(), it.view.c_str(),
				format_name(it.fmt), it.width, it.height, (unsigned long long)it.res.handle);
		}
	if (FILE *f = fopen((base + "index.txt").c_str(), "wb"))
	{
		fwrite(index.data(), 1, index.size(), f);
		fclose(f);
	}
	note(str("dump #%u written to %sdump", g_run.number, g_dir.c_str()));
}

// Frame boundary for the dump: once the list was submitted and a present has passed, wait for the GPU and write.
void dump_present()
{
	const uint32_t now = ++g_presents;
	if (g_dump_mode == DumpMode::idle || g_dump_mode == DumpMode::armed)
		return;
	command_queue *queue = nullptr;
	{
		std::lock_guard<std::mutex> lock(g_mutex);
		if (now - g_run.started > kGiveUpPresents)
		{
			note(str("dump #%u abandoned: command list never submitted", g_run.number));
			dump_release();
			return;
		}
		if (g_dump_mode != DumpMode::waiting || !g_run.submitted || now <= g_run.submitted_at)
			return;
		queue = g_run.queue;
	}
	// Outside the lock: the GPU may be waiting on work that other threads still have to record.
	queue->wait_idle();
	std::lock_guard<std::mutex> lock(g_mutex);
	dump_write();
	dump_release();
}

// ---- Stage 3a: automatic constant-buffer capture to find the camera jitter --------------------------------
// No hotkey: when amdlss_dx12_probe.cbcapture exists next to the add-on, the constant buffers of the first 16
// upscale dispatches (one per frame, after the title scene has settled) are copied to cbcapture/*.bin.

constexpr const char *kCbFlagFile = "amdlss_dx12_probe.cbcapture";
constexpr uint32_t kCbFrames = 16;
constexpr uint32_t kCbStartPresent = 120;
// A constant buffer view is at most 64 KB and a root CBV has no size at all, so the same cap serves both.
constexpr uint64_t kCbMaxBytes = 65536;
// Constant buffers in a default heap are read by the shader in this state; upload-heap buffers cannot be transitioned.
constexpr resource_usage kCbDefaultHeapState = resource_usage::constant_buffer;

struct CbItem
{
	uint32_t param = 0;
	std::string slot, heap;
	resource buffer = { 0 }, readback = { 0 };
	uint64_t offset = 0, size = 0;
	bool constants = false, barrier = false;
	std::vector<uint32_t> values;
};
struct CbCapture
{
	command_list *cmd_list = nullptr;
	device *dev = nullptr;
	command_queue *queue = nullptr;
	uint32_t frame = 0, present = 0, submitted_at = 0;
	bool submitted = false;
	std::string sizes;
	std::vector<CbItem> items;
};

std::atomic<bool> g_cb_active { false };
std::atomic<size_t> g_cb_pending_count { 0 };
std::vector<CbCapture> g_cb_pending;
std::unordered_set<uint64_t> g_live_buffers; // a descriptor can outlive its buffer; never copy from a destroyed one
uint32_t g_cb_started = 0, g_cb_last_present = 0;

void cb_note(const std::string &line)
{
	reshade::log::message(reshade::log::level::info, ("amdlss_dx12_probe: CB " + line).c_str());
	if (FILE *f = fopen(g_log_path.c_str(), "ab"))
	{
		fputs(("CB " + line + "\n").c_str(), f);
		fclose(f);
	}
}

const char *heap_name(memory_heap h)
{
	switch (h)
	{
	case memory_heap::default_: return "default";
	case memory_heap::upload: return "upload";
	case memory_heap::readback: return "readback";
	case memory_heap::custom: return "custom";
	default: return "unknown";
	}
}

void on_init_resource(device *, const resource_desc &desc, const subresource_data *, resource_usage, resource res)
{
	if (desc.type != resource_type::buffer)
		return;
	std::lock_guard<std::mutex> lock(g_mutex);
	g_live_buffers.insert(res.handle);
}
void on_destroy_resource(device *, resource res)
{
	std::lock_guard<std::mutex> lock(g_mutex);
	g_live_buffers.erase(res.handle);
}

void on_push_constants(command_list *cmd_list, shader_stage stages, pipeline_layout layout, uint32_t param, uint32_t first, uint32_t count, const void *values)
{
	if ((stages & shader_stage::compute) == static_cast<shader_stage>(0) || values == nullptr || count == 0 || first + count > 256)
		return;
	std::lock_guard<std::mutex> lock(g_mutex);
	std::vector<uint32_t> &v = compute_state(cmd_list, layout).consts[param];
	if (v.size() < first + count)
		v.resize(first + count);
	memcpy(v.data() + first, values, static_cast<size_t>(count) * 4);
}

void cb_add_range(std::vector<CbItem> &out, uint32_t param, const std::string &slot, const Info &info)
{
	if (info.resource == 0 || info.desc.type != resource_type::buffer || g_live_buffers.count(info.resource) == 0)
	{
		cb_note(str("skip p%u %s: buffer not resolved or already destroyed", param, slot.c_str()));
		return;
	}
	const uint64_t total = info.desc.buffer.size;
	const uint64_t avail = total > info.offset ? total - info.offset : 0;
	uint64_t size = info.range == UINT64_MAX || info.range > avail ? avail : info.range;
	if (size > kCbMaxBytes)
		size = kCbMaxBytes;
	if (size == 0)
	{
		cb_note(str("skip p%u %s: empty range", param, slot.c_str()));
		return;
	}
	CbItem it;
	it.param = param;
	it.slot = slot;
	it.heap = heap_name(info.desc.heap);
	it.buffer = { info.resource };
	it.offset = info.offset;
	it.size = size;
	it.barrier = info.desc.heap == memory_heap::default_;
	out.push_back(std::move(it));
}

// Every constant buffer the compute stage has bound: CBV ranges in descriptor tables, root CBVs and root constants.
void collect_cbs(device *dev, CmdState &st, std::vector<CbItem> &out)
{
	const auto layout = g_layouts.find(st.layout);
	if (layout == g_layouts.end())
		return;
	for (const auto &[param, table] : st.tables)
	{
		if (param >= layout->second.size())
			continue;
		for (const descriptor_range &r : layout->second[param].ranges)
		{
			if (r.type != descriptor_type::constant_buffer && r.type != descriptor_type::constant_buffer_with_dynamic_offset)
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
				const Entry &e = h->second[base + j];
				if (e.set && e.info != nullptr && e.info->buffer_range)
					cb_add_range(out, param, "b" + std::to_string(r.dx_register_index + j), *e.info);
			}
		}
	}
	for (const auto &[param, entries] : st.pushes)
		for (uint32_t k = 0; k < entries.size(); ++k)
			if (entries[k].type == descriptor_type::constant_buffer && entries[k].info != nullptr)
			{
				const bool known = param < layout->second.size() && !layout->second[param].ranges.empty();
				cb_add_range(out, param, "b" + std::to_string((known ? layout->second[param].ranges[0].dx_register_index : 0) + k), *entries[k].info);
			}
	for (const auto &[param, values] : st.consts)
	{
		CbItem it;
		it.param = param;
		it.slot = "rc";
		it.heap = "root";
		it.constants = true;
		it.values = values;
		it.size = values.size() * 4;
		out.push_back(std::move(it));
	}
}

bool has_slot(const DumpItem &it, const std::string &slot)
{
	size_t pos = 0;
	while (pos <= it.slots.size())
	{
		const size_t end = it.slots.find('+', pos);
		if (it.slots.substr(pos, end == std::string::npos ? end : end - pos) == slot)
			return true;
		if (end == std::string::npos)
			break;
		pos = end + 1;
	}
	return false;
}

// Render size is u3 (scene color) and output size is u0, per the stage 2 result.
std::string frame_sizes(device *dev, CmdState &st)
{
	std::vector<DumpItem> tex;
	collect_upscale_bindings(dev, st, tex, true);
	std::string render = "?", output = "?";
	for (const DumpItem &it : tex)
	{
		if (has_slot(it, "u3"))
			render = str("%ux%u", it.width, it.height);
		if (has_slot(it, "u0"))
			output = str("%ux%u", it.width, it.height);
	}
	return "render=" + render + " output=" + output;
}

void cb_record(command_list *cmd_list, CbCapture &cap)
{
	for (size_t i = 0; i < cap.items.size();)
	{
		CbItem &it = cap.items[i];
		if (!it.constants && !(cap.dev->create_resource(resource_desc(it.size, memory_heap::readback, resource_usage::copy_dest), nullptr, resource_usage::copy_dest, &it.readback) && it.readback.handle != 0))
		{
			cb_note(str("skip p%u %s: cannot create readback buffer", it.param, it.slot.c_str()));
			cap.items.erase(cap.items.begin() + i);
			continue;
		}
		++i;
	}
	std::vector<resource> res;
	for (const CbItem &it : cap.items)
		if (it.barrier && std::find_if(res.begin(), res.end(), [&](resource r) { return r.handle == it.buffer.handle; }) == res.end())
			res.push_back(it.buffer);
	const std::vector<resource_usage> cb_state(res.size(), kCbDefaultHeapState), src_state(res.size(), resource_usage::copy_source);
	if (!res.empty())
		cmd_list->barrier(static_cast<uint32_t>(res.size()), res.data(), cb_state.data(), src_state.data());
	for (const CbItem &it : cap.items)
		if (!it.constants)
			cmd_list->copy_buffer_region(it.buffer, it.offset, it.readback, 0, it.size);
	if (!res.empty())
		cmd_list->barrier(static_cast<uint32_t>(res.size()), res.data(), src_state.data(), cb_state.data());
}

void cb_dispatch(command_list *cmd_list)
{
	if (!g_cb_active)
		return;
	std::lock_guard<std::mutex> lock(g_mutex);
	const uint32_t now = g_presents;
	if (!g_cb_active || now == g_cb_last_present)
		return;
	CmdState &st = g_cmds[cmd_list];
	const auto pipe = g_pipelines.find(st.pipeline);
	if (pipe == g_pipelines.end() || pipe->second.hash != kUpscaleHash)
		return;

	g_cb_last_present = now;
	CbCapture cap;
	cap.cmd_list = cmd_list;
	cap.dev = cmd_list->get_device();
	cap.frame = g_cb_started++;
	cap.present = now;
	if (g_cb_started >= kCbFrames)
		g_cb_active = false;
	cap.sizes = frame_sizes(cap.dev, st);
	collect_cbs(cap.dev, st, cap.items);
	cb_record(cmd_list, cap);
	cb_note(str("frame %u present %u: recorded %zu constant buffers (%s)", cap.frame, now, cap.items.size(), cap.sizes.c_str()));
	g_cb_pending.push_back(std::move(cap));
	g_cb_pending_count = g_cb_pending.size();
}

void cb_release(CbCapture &cap)
{
	for (CbItem &it : cap.items)
		if (it.readback.handle != 0 && cap.dev != nullptr)
			cap.dev->destroy_resource(it.readback);
}

void cb_write(CbCapture &cap)
{
	CreateDirectoryA((g_dir + "cbcapture").c_str(), nullptr);
	const std::string dir = g_dir + "cbcapture\\";
	const bool fresh = GetFileAttributesA((dir + "index.txt").c_str()) == INVALID_FILE_ATTRIBUTES;
	std::string index;
	if (fresh)
		index = "# frame, present, param, slot, resource, offset, size, heap, file (tab separated)\n";
	index += str("# frame %u present %u %s\n", cap.frame, cap.present, cap.sizes.c_str());
	for (CbItem &it : cap.items)
	{
		const void *data = it.values.data();
		void *mapped = nullptr;
		if (!it.constants)
		{
			if (!cap.dev->map_buffer_region(it.readback, 0, it.size, map_access::read_only, &mapped) || mapped == nullptr)
			{
				cb_note(str("skip p%u %s: cannot map readback buffer", it.param, it.slot.c_str()));
				continue;
			}
			data = mapped;
		}
		const std::string name = str("frame_%02u_p%u_%s_%llu.bin", cap.frame, it.param, it.slot.c_str(), (unsigned long long)it.size);
		bool ok = false;
		if (FILE *f = fopen((dir + name).c_str(), "wb"))
		{
			ok = fwrite(data, 1, static_cast<size_t>(it.size), f) == it.size;
			ok = fclose(f) == 0 && ok;
		}
		if (mapped != nullptr)
			cap.dev->unmap_buffer_region(it.readback);
		if (!ok)
		{
			cb_note("skip " + name + ": cannot write");
			continue;
		}
		index += str("%u\t%u\t%u\t%s\t%llx\t%llu\t%llu\t%s\t%s\n", cap.frame, cap.present, it.param, it.slot.c_str(),
			(unsigned long long)it.buffer.handle, (unsigned long long)it.offset, (unsigned long long)it.size, it.heap.c_str(), name.c_str());
	}
	if (FILE *f = fopen((dir + "index.txt").c_str(), "ab"))
	{
		fwrite(index.data(), 1, index.size(), f);
		fclose(f);
	}
	cb_note(str("frame %u written to %scbcapture", cap.frame, g_dir.c_str()));
}

// Per present: arm after the settle time if the flag file exists, then write captures whose list was submitted.
void cb_present(uint32_t now)
{
	if (now == kCbStartPresent && GetFileAttributesA((g_dir + kCbFlagFile).c_str()) != INVALID_FILE_ATTRIBUTES)
	{
		g_cb_active = true;
		cb_note("armed: capturing the upscale constant buffers for 16 frames");
	}
	if (g_cb_pending_count == 0)
		return;
	std::vector<CbCapture> ready;
	{
		std::lock_guard<std::mutex> lock(g_mutex);
		for (size_t i = 0; i < g_cb_pending.size();)
		{
			CbCapture &cap = g_cb_pending[i];
			const bool stale = !cap.submitted && now - cap.present > kGiveUpPresents;
			const bool done = cap.submitted && now > cap.submitted_at;
			if (stale)
			{
				cb_note(str("frame %u abandoned: command list never submitted", cap.frame));
				cb_release(cap);
			}
			if (done)
				ready.push_back(std::move(cap));
			if (stale || done)
				g_cb_pending.erase(g_cb_pending.begin() + i);
			else
				++i;
		}
		g_cb_pending_count = g_cb_pending.size();
	}
	// Outside the lock: the GPU may be waiting on work that other threads still have to record.
	for (CbCapture &cap : ready)
		cap.queue->wait_idle();
	std::lock_guard<std::mutex> lock(g_mutex);
	for (CbCapture &cap : ready)
	{
		cb_write(cap);
		cb_release(cap);
	}
}

void on_execute_command_list(command_queue *queue, command_list *cmd_list)
{
	if (g_cb_pending_count != 0)
	{
		std::lock_guard<std::mutex> lock(g_mutex);
		for (CbCapture &cap : g_cb_pending)
			if (cap.cmd_list == cmd_list && !cap.submitted)
			{
				cap.submitted = true;
				cap.queue = queue;
				cap.submitted_at = g_presents;
			}
	}
	if (g_dump_mode != DumpMode::waiting)
		return;
	std::lock_guard<std::mutex> lock(g_mutex);
	if (g_dump_mode == DumpMode::waiting && g_run.cmd_list == cmd_list && !g_run.submitted)
	{
		g_run.submitted = true;
		g_run.queue = queue;
		g_run.submitted_at = g_presents;
	}
}

void on_barrier(command_list *cmd_list, uint32_t count, const resource *res, const resource_usage *, const resource_usage *new_states)
{
	dump_event(cmd_list, false, count, res, new_states);
}
void on_close_command_list(command_list *cmd_list)
{
	dump_event(cmd_list, false);
}

void on_reset_command_list(command_list *cmd_list)
{
	std::lock_guard<std::mutex> lock(g_mutex);
	g_cmds.erase(cmd_list);
	for (size_t i = 0; i < g_cb_pending.size();)
		if (g_cb_pending[i].cmd_list == cmd_list && !g_cb_pending[i].submitted)
		{
			cb_note(str("frame %u abandoned: command list was reset before submission", g_cb_pending[i].frame));
			cb_release(g_cb_pending[i]);
			g_cb_pending.erase(g_cb_pending.begin() + i);
			g_cb_pending_count = g_cb_pending.size();
		}
		else
			++i;
	if (g_dump_mode != DumpMode::idle && g_dump_mode != DumpMode::armed && g_run.cmd_list == cmd_list && !g_run.submitted)
	{
		note(str("dump #%u abandoned: command list was reset before submission", g_run.number));
		dump_release();
	}
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
	if (dsv.handle != 0 && g_last_dsv.exchange(dsv.handle) != dsv.handle)
	{
		std::lock_guard<std::mutex> lock(g_mutex);
		g_dsv_history.push_back(dsv.handle);
		if (g_dsv_history.size() > kDsvHistory)
			g_dsv_history.erase(g_dsv_history.begin());
	}
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
	dump_event(cmd_list, true);
	cb_dispatch(cmd_list);
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
bool on_draw(command_list *cmd_list, uint32_t, uint32_t, uint32_t, uint32_t)
{
	dump_event(cmd_list, false);
	if (g_mode == Mode::capturing)
		++g_draws;
	return false;
}
bool on_draw_indexed(command_list *cmd_list, uint32_t, uint32_t, uint32_t, int32_t, uint32_t)
{
	dump_event(cmd_list, false);
	if (g_mode == Mode::capturing)
		++g_draws;
	return false;
}
// ExecuteIndirect: UE5 issues its GPU-driven passes this way, so the group counts are unknown here.
bool on_indirect(command_list *cmd_list, indirect_command type, resource buffer, uint64_t offset, uint32_t draw_count, uint32_t)
{
	dump_event(cmd_list, type == indirect_command::dispatch);
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
	dump_present();
	cb_present(g_presents);
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

	static bool dump_was_down = false;
	const bool dump_down = runtime->is_key_pressed(kDumpHotkey) || (GetAsyncKeyState(kDumpHotkey) & 0x8000) != 0;
	DumpMode idle = DumpMode::idle;
	if (dump_down && !dump_was_down && g_dump_mode.compare_exchange_strong(idle, DumpMode::armed))
		note("dump armed: waiting for the upscale dispatch");
	dump_was_down = dump_down;
}
} // namespace

extern "C"
{
__declspec(dllexport) const char *NAME = "AMDLSS DX12 probe";
__declspec(dllexport) const char *DESCRIPTION = "F10 logs one frame of compute dispatches and their bound resources to amdlss_dx12_probe.log; F11 dumps the upscale pass textures to dump/*.dds; amdlss_dx12_probe.cbcapture captures its constant buffers to cbcapture/.";
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
		g_dir = g_log_path;
		g_log_path += "amdlss_dx12_probe.log";

		reshade::register_event<addon_event::init_pipeline>(on_init_pipeline);
		reshade::register_event<addon_event::destroy_pipeline>(on_destroy_pipeline);
		reshade::register_event<addon_event::init_pipeline_layout>(on_init_pipeline_layout);
		reshade::register_event<addon_event::destroy_pipeline_layout>(on_destroy_pipeline_layout);
		reshade::register_event<addon_event::init_resource_view>(on_init_resource_view);
		reshade::register_event<addon_event::destroy_resource_view>(on_destroy_resource_view);
		reshade::register_event<addon_event::update_descriptor_tables>(on_update_descriptor_tables);
		reshade::register_event<addon_event::copy_descriptor_tables>(on_copy_descriptor_tables);
		reshade::register_event<addon_event::init_resource>(on_init_resource);
		reshade::register_event<addon_event::destroy_resource>(on_destroy_resource);
		reshade::register_event<addon_event::push_constants>(on_push_constants);
		reshade::register_event<addon_event::barrier>(on_barrier);
		reshade::register_event<addon_event::close_command_list>(on_close_command_list);
		reshade::register_event<addon_event::execute_command_list>(on_execute_command_list);
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
