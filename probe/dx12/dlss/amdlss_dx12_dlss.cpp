/*
 * amdlss_dx12_dlss: ReShade add-on for stage 3b of the DX12 spike (odd/tasks/dx12-spike.md).
 *
 * Replaces the game's TSR upscale dispatch (compute shader hash kUpscaleHash) with DLSS Super Resolution. On that
 * dispatch it finds scene color, velocity, depth and the output through the descriptor heap shadow (the same approach
 * as the probe add-on), reads the camera jitter from the root CBV, decodes UE's velocity into DLSS motion vectors,
 * evaluates NGX on the game's own command list and returns true so ReShade skips the original dispatch. NGX and the
 * decode pass change the compute root signature, pipeline, descriptor heaps and root arguments of the list, so all of
 * it is set back afterwards from the shadowed state. If anything is missing the game renders normally.
 *
 * Known gaps, logged once: translucency u4 is not used and u2 (history metadata) is not written. u1, TSR's half-size
 * output that bloom and eye adaptation read, is filled from the DLSS output (ini fill_half_res).
 */
// Dear ImGui 1.92.5 (docking) headers only: the widgets run in ReShade's own ImGui through its function table.
#define ImTextureID ImU64
#include <imgui.h>
#include <reshade.hpp>

#include <d3d12.h>

#include <nvsdk_ngx_helpers.h>

#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "decode_dxil.h"
#include "half_dxil.h"
#include "logic.hpp"

using namespace reshade::api;
using reshade::addon_event;

static_assert(NVSDK_NGX_DLSS_Hint_Render_Preset_J == 10 && NVSDK_NGX_DLSS_Hint_Render_Preset_K == 11 && NVSDK_NGX_DLSS_Hint_Render_Preset_L == 12 &&
	NVSDK_NGX_DLSS_Hint_Render_Preset_M == 13, "logic::kRenderPresets must match NVSDK_NGX_DLSS_Hint_Render_Preset");

namespace
{
// Compute shader found in stage 1: one thread per output pixel (see "Stage 1 result" in odd/tasks/dx12-spike.md).
constexpr uint64_t kUpscaleHash = 0x1ff10c3ce95d3bcbull;
// Root parameter 1 is the pass's descriptor table; the registers below are the slots found in stage 2.
constexpr uint32_t kBindingsParam = 1;
constexpr uint32_t kRegOutput = 0, kRegHalf = 1, kRegColor = 3, kRegTranslucency = 4, kRegVelocity = 6;
// Stage 3a (T7): TSR's constants (jitter at byte 256, viewports, camera cut, velocity scale) are the root CBV b0 at root
// parameter 2; the field offsets are in logic.hpp.
constexpr uint32_t kJitterParam = 2;
// Fixed NGX project id for this engine-custom integration.
constexpr const char *kProjectId = "5d0c1e7a-3f24-4b88-9a51-7c2e6d0b4f93";
constexpr uint32_t kUnboundedCap = 64;
constexpr uint32_t kMaxHeapDescriptors = 4000000;
constexpr uint32_t kDecodeRing = 64; // descriptor sets for the decode pass, reused round robin across frames in flight
constexpr uint32_t kDescriptorsPerSet = 5; // decode: 3 SRVs + 2 UAVs; the half-size fill uses the first two

// ---- descriptor shadow (same approach as the probe add-on) --------------------------------------------------

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
struct LayoutParam
{
	pipeline_layout_param_type type = pipeline_layout_param_type::descriptor_table;
	std::vector<descriptor_range> ranges;
};
struct CmdState
{
	uint64_t pipeline = 0, layout = 0, heap_view = 0, heap_sampler = 0;
	std::map<uint32_t, descriptor_table> tables;
	std::map<uint32_t, std::vector<Entry>> pushes;
	std::map<uint32_t, std::vector<uint32_t>> consts;
};

std::mutex g_mutex;
std::unordered_map<uint64_t, uint64_t> g_pipelines; // compute pipeline -> hash of its bytecode
std::unordered_map<uint64_t, std::vector<LayoutParam>> g_layouts;
std::unordered_map<uint64_t, std::vector<Entry>> g_heaps;
std::unordered_map<uint64_t, std::shared_ptr<const Info>> g_views;
std::unordered_map<const command_list *, CmdState> g_cmds;
std::unordered_set<uint64_t> g_live_buffers; // never map or restore a destroyed buffer

// While our own commands go through the game's list, ReShade calls our handlers again; they must ignore them.
thread_local bool t_busy = false;

// ---- config and log -------------------------------------------------------------------------------------------

struct Config
{
	bool enabled = true;
	float jitter_sign_x = 1, jitter_sign_y = 1;
	// NDC delta -> render pixels is (mv_scale_x * width, mv_scale_y * height); UE velocity points forward in time and
	// y up, DLSS wants previous minus current with y down, hence the signs.
	float mv_scale_x = -0.5f, mv_scale_y = 0.5f;
	uint32_t log_every = 300, jitter_cycle = 8;
	bool fill_half_res = true; // u1 is written by TSR and read by bloom and eye adaptation
	// Once DLSS has run, never let TSR run on history that DLSS frames wrote: skip it even when no evaluate is possible.
	bool never_fallback_after_start = true;
	bool composite_translucency = true; // u4 over u3 before DLSS
	bool mv_gamma = true;               // true: what UE's TSR shader does (e*abs(e)*0.5); false: only the linear unbias
	bool mv_scale_from_game = true;     // pixel scale of the velocity from TSR's b0; false: mv_scale_x/y times the viewport
	int render_preset = 0;              // NVSDK_NGX_DLSS_Hint_Render_Preset value, set on all five per-mode hints; 0 = Default
	bool auto_exposure = true;          // the AutoExposure create flag
};
// Written under g_mutex (the ini reload and the overlay); the dispatch path copies it into its Snapshot under g_mutex.
Config g_cfg;
std::string g_dir, g_log_path, g_ini_path;
FILETIME g_ini_time = {};
bool g_reset_next = false; // set when TSR ran in between or the ini changed: DLSS must not trust its history

void log_line(const char *fmt, ...)
{
	char buf[4096];
	va_list args;
	va_start(args, fmt);
	vsnprintf(buf, sizeof(buf), fmt, args);
	va_end(args);
	if (FILE *f = fopen(g_log_path.c_str(), "ab"))
	{
		fputs(buf, f);
		fputc('\n', f);
		fclose(f);
	}
	reshade::log::message(reshade::log::level::info, (std::string("amdlss_dx12_dlss: ") + buf).c_str());
}

// True for the first `limit` calls with this key; a failing frame would otherwise flood the log.
bool first_n(const char *key, int limit)
{
	static std::unordered_map<std::string, int> seen;
	static std::mutex m;
	std::lock_guard<std::mutex> lock(m);
	return ++seen[key] <= limit;
}
bool first_time(const char *key)
{
	return first_n(key, 1);
}

std::string str_format(const char *fmt, ...)
{
	char buf[1024];
	va_list args;
	va_start(args, fmt);
	vsnprintf(buf, sizeof(buf), fmt, args);
	va_end(args);
	return buf;
}

std::string narrow(const wchar_t *w)
{
	std::string out;
	for (; w != nullptr && *w; ++w)
		out += *w < 128 ? static_cast<char>(*w) : '?';
	return out;
}

// ---- shadow events --------------------------------------------------------------------------------------------

uint64_t fnv1a(const void *data, size_t size)
{
	uint64_t h = 14695981039346656037ull;
	for (size_t i = 0; i < size; ++i)
		h = (h ^ static_cast<const uint8_t *>(data)[i]) * 1099511628211ull;
	return h;
}

bool heap_offset(device *dev, descriptor_table table, uint32_t binding, uint32_t array_offset, uint64_t &heap, uint32_t &offset)
{
	descriptor_heap h = { 0 };
	dev->get_descriptor_heap_offset(table, binding, array_offset, &h, &offset);
	heap = h.handle;
	return heap != 0;
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

// A new root signature drops what was bound before.
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

// The game's own TSR shader, written once so its DXIL can be disassembled (disasm.sh): how it decodes velocity and how
// it uses the jitter is then read from UE's code instead of guessed.
void dump_tsr_bytecode(const void *code, size_t size)
{
	static std::atomic<bool> dumped { false };
	if (dumped.exchange(true))
		return;
	const std::string path = g_dir + "tsr_1ff10c3ce95d3bcb.dxil";
	FILE *f = fopen(path.c_str(), "wb");
	const bool ok = f != nullptr && fwrite(code, 1, size, f) == size;
	if (f != nullptr)
		fclose(f);
	log_line("TSR bytecode (%zu bytes) %s %s", size, ok ? "written to" : "could not be written to", path.c_str());
}

void on_init_pipeline(device *, pipeline_layout, uint32_t count, const pipeline_subobject *subobjects, pipeline pipe)
{
	for (uint32_t i = 0; i < count; ++i)
	{
		if (subobjects[i].type != pipeline_subobject_type::compute_shader || subobjects[i].count == 0)
			continue;
		const auto *shader = static_cast<const shader_desc *>(subobjects[i].data);
		const uint64_t hash = shader->code != nullptr ? fnv1a(shader->code, shader->code_size) : 0;
		if (hash == kUpscaleHash)
			dump_tsr_bytecode(shader->code, shader->code_size);
		std::lock_guard<std::mutex> lock(g_mutex);
		g_pipelines[pipe.handle] = hash;
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

bool on_update_descriptor_tables(device *dev, uint32_t count, const descriptor_table_update *updates)
{
	if (t_busy)
		return false;
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
	if (t_busy)
		return false;
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
	if (t_busy || (stages & pipeline_stage::compute_shader) == static_cast<pipeline_stage>(0))
		return;
	std::lock_guard<std::mutex> lock(g_mutex);
	g_cmds[cmd_list].pipeline = pipe.handle;
}

void on_push_descriptors(command_list *cmd_list, shader_stage stages, pipeline_layout layout, uint32_t param, const descriptor_table_update &update)
{
	if (t_busy || (stages & shader_stage::compute) == static_cast<shader_stage>(0))
		return;
	std::vector<Entry> entries;
	for (uint32_t k = 0; k < update.count; ++k)
		entries.push_back(make_entry(cmd_list->get_device(), update.type, update.descriptors, k));
	std::lock_guard<std::mutex> lock(g_mutex);
	compute_state(cmd_list, layout).pushes[param] = std::move(entries);
}

void on_push_constants(command_list *cmd_list, shader_stage stages, pipeline_layout layout, uint32_t param, uint32_t first, uint32_t count, const void *values)
{
	if (t_busy || (stages & shader_stage::compute) == static_cast<shader_stage>(0) || values == nullptr || count == 0 || first + count > 256)
		return;
	std::lock_guard<std::mutex> lock(g_mutex);
	std::vector<uint32_t> &v = compute_state(cmd_list, layout).consts[param];
	if (v.size() < first + count)
		v.resize(first + count);
	memcpy(v.data() + first, values, static_cast<size_t>(count) * 4);
}

// Tables of every stage tell which descriptor heaps the game has set; compute tables are also kept for the restore.
void on_bind_descriptor_tables(command_list *cmd_list, shader_stage stages, pipeline_layout layout, uint32_t first, uint32_t count, const descriptor_table *tables, uint32_t, const uint32_t *)
{
	if (t_busy)
		return;
	device *dev = cmd_list->get_device();
	std::lock_guard<std::mutex> lock(g_mutex);
	CmdState &st = g_cmds[cmd_list];
	for (uint32_t i = 0; i < count; ++i)
	{
		uint64_t heap;
		uint32_t offset;
		if (tables[i].handle != 0 && heap_offset(dev, tables[i], 0, 0, heap, offset))
		{
			const D3D12_DESCRIPTOR_HEAP_TYPE type = reinterpret_cast<ID3D12DescriptorHeap *>(heap)->GetDesc().Type;
			(type == D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER ? st.heap_sampler : st.heap_view) = heap;
		}
	}
	if ((stages & shader_stage::compute) != static_cast<shader_stage>(0))
	{
		CmdState &cs = compute_state(cmd_list, layout);
		for (uint32_t i = 0; i < count; ++i)
			cs.tables[first + i] = tables[i];
	}
}

// ---- the pass's bindings ----------------------------------------------------------------------------------------

struct Tex
{
	bool ok = false, uav_view = false; // uav_view: the descriptor is a UAV, so the resource sits in the UAV state
	bool seen = false;                 // a descriptor was found in that slot (it may still not be a usable texture)
	std::string why;                   // why a seen descriptor is not usable
	uint64_t view_handle = 0;
	resource res = { 0 };
	uint32_t width = 0, height = 0;
	format view_format = format::unknown, res_format = format::unknown;
	resource_view_type view_type = resource_view_type::unknown;
};
struct Bindings
{
	Tex output, half, color, translucency, velocity, depth;
};

Tex tex_from(const Entry &e)
{
	Tex t;
	t.seen = true;
	t.view_handle = e.handle;
	if (e.info == nullptr)
		t.why = "descriptor has no creation snapshot (a view made before the add-on loaded?)";
	else if (e.info->buffer_range)
		t.why = "a buffer range, not a texture";
	else if (e.info->resource == 0)
		t.why = "a view of a null resource";
	else if (e.info->desc.type != resource_type::texture_2d)
		t.why = "not a 2D texture";
	if (!t.why.empty())
		return t;
	t.ok = true;
	t.uav_view = e.type == descriptor_type::unordered_access_view || e.type == descriptor_type::buffer_unordered_access_view;
	t.res = { e.info->resource };
	t.width = e.info->desc.texture.width;
	t.height = e.info->desc.texture.height;
	t.view_format = e.info->view_format;
	t.res_format = e.info->desc.texture.format;
	t.view_type = e.info->view_type;
	return t;
}

std::string describe(const char *slot, const Tex &t)
{
	char buf[384];
	if (!t.seen)
		snprintf(buf, sizeof(buf), "%s: no descriptor written in that slot of the table", slot);
	else if (!t.ok)
		snprintf(buf, sizeof(buf), "%s: view %llx unusable, %s", slot, static_cast<unsigned long long>(t.view_handle), t.why.c_str());
	else
		snprintf(buf, sizeof(buf), "%s: resource %llx %ux%u dxgi format %u (view format %u, view type %u, %s descriptor)", slot, static_cast<unsigned long long>(t.res.handle),
			t.width, t.height, static_cast<unsigned>(t.res_format), static_cast<unsigned>(t.view_format), static_cast<unsigned>(t.view_type), t.uav_view ? "UAV" : "SRV");
	return buf;
}

std::string describe_all(const Bindings &b)
{
	return describe("u0 output", b.output) + "; " + describe("u1 half-size", b.half) + "; " + describe("u3 color", b.color) + "; " + describe("u4 translucency", b.translucency) + "; " +
		describe("u6 velocity", b.velocity) + "; " + describe("depth", b.depth);
}

// Depth buffers bound for rendering this session, newest last. Used when the pass's own depth slot is the wrong size.
constexpr size_t kDsvHistory = 32;
std::vector<uint64_t> g_dsv_history;
std::atomic<uint64_t> g_last_dsv { 0 };

void on_bind_render_targets(command_list *, uint32_t, const resource_view *, resource_view dsv)
{
	if (t_busy || dsv.handle == 0 || g_last_dsv.exchange(dsv.handle) == dsv.handle)
		return;
	std::lock_guard<std::mutex> lock(g_mutex);
	g_dsv_history.push_back(dsv.handle);
	if (g_dsv_history.size() > kDsvHistory)
		g_dsv_history.erase(g_dsv_history.begin());
}

// The most recent depth buffer whose size is the render size (the probe's render-size depth lookup).
bool find_depth_of_size(uint32_t w, uint32_t h, Tex &out)
{
	for (auto it = g_dsv_history.rbegin(); it != g_dsv_history.rend(); ++it)
	{
		const auto v = g_views.find(*it);
		if (v == g_views.end() || v->second->resource == 0 || v->second->desc.type != resource_type::texture_2d ||
			v->second->desc.texture.width != w || v->second->desc.texture.height != h)
			continue;
		out = Tex();
		out.seen = out.ok = true;
		out.view_handle = *it;
		out.res = { v->second->resource };
		out.width = w;
		out.height = h;
		out.view_format = v->second->view_format;
		out.res_format = v->second->desc.texture.format;
		out.view_type = v->second->view_type;
		out.uav_view = false; // its real state is unknown; NON_PIXEL_SHADER_RESOURCE is assumed
		return true;
	}
	return false;
}

bool find_bindings(device *dev, const CmdState &st, Bindings &b, std::string &why)
{
	const auto layout = g_layouts.find(st.layout);
	const auto table = st.tables.find(kBindingsParam);
	if (layout == g_layouts.end() || kBindingsParam >= layout->second.size())
	{
		why = "the bound root signature is unknown or has no parameter 1";
		return false;
	}
	if (table == st.tables.end())
	{
		why = "no descriptor table was bound at root parameter 1";
		return false;
	}
	for (const descriptor_range &r : layout->second[kBindingsParam].ranges)
	{
		if (r.type != descriptor_type::unordered_access_view && r.type != descriptor_type::shader_resource_view)
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
			const uint32_t reg = r.dx_register_index + j;
			Tex *target = reg == kRegOutput ? &b.output : reg == kRegHalf ? &b.half : reg == kRegColor ? &b.color : reg == kRegTranslucency ? &b.translucency :
				reg == kRegVelocity ? &b.velocity : nullptr;
			if (target != nullptr)
				*target = tex_from(e);
		}
	}
	if (!(b.output.ok && b.color.ok && b.velocity.ok))
	{
		why = "not all of u0, u3 and u6 are usable textures";
		return false;
	}
	return true;
}

// ---- jitter -------------------------------------------------------------------------------------------------------

// Option A: the root CBV lives in an upload heap and UE wrote it on the CPU before recording this dispatch, so the
// fields the add-on needs (jitter, viewports, camera cut, velocity scale) can be read now, in one map of the first
// logic::kTsrConstantsBytes bytes of b0. Option B (fallback): once a read has been matched to the Halton sequence,
// predict the jitter and reuse the last viewport and scale.
struct JitterState
{
	int phase = -1;
	uint64_t phase_eval = 0;
	bool using_fallback = false;
} g_jitter;
logic::TsrConstants g_tsr_last;
bool g_tsr_last_valid = false;

bool read_tsr_constants(device *dev, const CmdState &st, logic::TsrConstants &out, std::string &why)
{
	const auto it = st.pushes.find(kJitterParam);
	if (it == st.pushes.end() || it->second.empty() || it->second[0].info == nullptr || !it->second[0].info->buffer_range)
	{
		why = "no root CBV at the TSR constants root parameter";
		return false;
	}
	const Info &info = *it->second[0].info;
	if (info.resource == 0 || g_live_buffers.count(info.resource) == 0)
	{
		why = "constant buffer unresolved or destroyed";
		return false;
	}
	if (info.desc.heap != memory_heap::upload)
	{
		why = "constant buffer is not in an upload heap";
		return false;
	}
	if (info.offset + logic::kTsrConstantsBytes > info.desc.buffer.size)
	{
		why = "the TSR constants are past the end of the buffer";
		return false;
	}
	void *data = nullptr;
	if (!dev->map_buffer_region({ info.resource }, info.offset, logic::kTsrConstantsBytes, map_access::read_only, &data) || data == nullptr)
	{
		why = "map_buffer_region failed";
		return false;
	}
	uint8_t copy[logic::kTsrConstantsBytes];
	memcpy(copy, data, sizeof(copy));
	dev->unmap_buffer_region({ info.resource });
	if (!logic::parse_tsr_constants(copy, sizeof(copy), out))
	{
		why = "the bytes read are not plausible TSR constants (viewport, jitter or velocity scale out of range)";
		return false;
	}
	return true;
}

// ---- native D3D12: decode pass, NGX, state restore -----------------------------------------------------------------

struct Native
{
	ID3D12Device *dev = nullptr;
	ID3D12RootSignature *rs = nullptr, *half_rs = nullptr;
	ID3D12PipelineState *pso = nullptr, *half_pso = nullptr;
	bool half_ready = false;
	ID3D12DescriptorHeap *heap = nullptr;
	UINT inc = 0;
	uint32_t ring = 0;
	ID3D12Resource *mv = nullptr;   // r16g16_float, render size; always left in the UAV state between frames
	ID3D12Resource *comp = nullptr; // r16g16b16a16_float, render size: u3 with translucency u4 composited over it
	uint32_t mv_w = 0, mv_h = 0, comp_w = 0, comp_h = 0;
	std::vector<ID3D12Resource *> retired; // replaced on a size change; may still be read by frames in flight
	bool ready = false, failed = false;
} g_native;

struct NgxState
{
	bool init_tried = false, ok = false;
	NVSDK_NGX_Parameter *params = nullptr;
	NVSDK_NGX_Handle *feature = nullptr;
	std::vector<NVSDK_NGX_Handle *> retired; // released at process exit by the OS; releasing mid-flight is unsafe
	uint32_t rw = 0, rh = 0, ow = 0, oh = 0;
	bool output_subrects = false;
	int render_preset = 0;      // what the current feature was created with; a change recreates it
	bool auto_exposure = true;
	uint64_t evals = 0;
} g_ngx;

void NVSDK_CONV ngx_log(const char *message, NVSDK_NGX_Logging_Level, NVSDK_NGX_Feature)
{
	if (FILE *f = fopen(g_log_path.c_str(), "ab"))
	{
		fputs("NGX ", f);
		fputs(message, f);
		fclose(f);
	}
}

bool ensure_native(ID3D12Device *dev)
{
	if (g_native.ready)
		return true;
	if (g_native.failed)
		return false;
	g_native.failed = true;
	g_native.dev = dev;
	// The root signature is embedded in the shader, so CreateRootSignature takes the DXIL container itself.
	if (FAILED(dev->CreateRootSignature(0, g_decode_dxil, g_decode_dxil_len, IID_PPV_ARGS(&g_native.rs))))
	{
		log_line("decode pass: CreateRootSignature failed");
		return false;
	}
	D3D12_COMPUTE_PIPELINE_STATE_DESC pso = {};
	pso.pRootSignature = g_native.rs;
	pso.CS = { g_decode_dxil, g_decode_dxil_len };
	if (FAILED(dev->CreateComputePipelineState(&pso, IID_PPV_ARGS(&g_native.pso))))
	{
		log_line("decode pass: CreateComputePipelineState failed (does the device accept this DXIL?)");
		return false;
	}
	D3D12_DESCRIPTOR_HEAP_DESC hd = {};
	hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV;
	hd.NumDescriptors = kDescriptorsPerSet * kDecodeRing;
	hd.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
	if (FAILED(dev->CreateDescriptorHeap(&hd, IID_PPV_ARGS(&g_native.heap))))
	{
		log_line("decode pass: CreateDescriptorHeap failed");
		return false;
	}
	g_native.inc = dev->GetDescriptorHandleIncrementSize(D3D12_DESCRIPTOR_HEAP_TYPE_CBV_SRV_UAV);
	// The half-size fill is optional: if its shader is refused, only that fill is skipped.
	D3D12_COMPUTE_PIPELINE_STATE_DESC half = {};
	if (SUCCEEDED(dev->CreateRootSignature(0, g_half_dxil, g_half_dxil_len, IID_PPV_ARGS(&g_native.half_rs))))
	{
		half.pRootSignature = g_native.half_rs;
		half.CS = { g_half_dxil, g_half_dxil_len };
		g_native.half_ready = SUCCEEDED(dev->CreateComputePipelineState(&half, IID_PPV_ARGS(&g_native.half_pso)));
	}
	if (!g_native.half_ready)
		log_line("half-res fill: its shader could not be created, u1 will not be filled");
	g_native.failed = false;
	g_native.ready = true;
	log_line("decode pass ready (%zu bytes of DXIL)", static_cast<size_t>(g_decode_dxil_len));
	return true;
}

// A default-heap UAV texture of the render size that is always left in the UAV state between frames.
bool ensure_texture(ID3D12Resource *&res, uint32_t &cur_w, uint32_t &cur_h, DXGI_FORMAT fmt, uint32_t w, uint32_t h, const char *what)
{
	if (res != nullptr && cur_w == w && cur_h == h)
		return true;
	if (res != nullptr)
		g_native.retired.push_back(res);
	res = nullptr;
	D3D12_HEAP_PROPERTIES hp = {};
	hp.Type = D3D12_HEAP_TYPE_DEFAULT;
	D3D12_RESOURCE_DESC rd = {};
	rd.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
	rd.Width = w;
	rd.Height = h;
	rd.DepthOrArraySize = 1;
	rd.MipLevels = 1;
	rd.Format = fmt;
	rd.SampleDesc.Count = 1;
	rd.Layout = D3D12_TEXTURE_LAYOUT_UNKNOWN;
	rd.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;
	if (FAILED(g_native.dev->CreateCommittedResource(&hp, D3D12_HEAP_FLAG_NONE, &rd, D3D12_RESOURCE_STATE_UNORDERED_ACCESS, nullptr, IID_PPV_ARGS(&res))))
	{
		log_line("%s texture %ux%u: CreateCommittedResource failed", what, w, h);
		return false;
	}
	cur_w = w;
	cur_h = h;
	log_line("%s texture created: %ux%u", what, w, h);
	return true;
}

bool ensure_mv_texture(uint32_t w, uint32_t h)
{
	return ensure_texture(g_native.mv, g_native.mv_w, g_native.mv_h, DXGI_FORMAT_R16G16_FLOAT, w, h, "motion vector (r16g16_float)");
}
bool ensure_comp_texture(uint32_t w, uint32_t h)
{
	return ensure_texture(g_native.comp, g_native.comp_w, g_native.comp_h, DXGI_FORMAT_R16G16B16A16_FLOAT, w, h, "composited color (r16g16b16a16_float)");
}

D3D12_RESOURCE_BARRIER transition(ID3D12Resource *r, D3D12_RESOURCE_STATES from, D3D12_RESOURCE_STATES to)
{
	D3D12_RESOURCE_BARRIER b = {};
	b.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
	b.Transition.pResource = r;
	b.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
	b.Transition.StateBefore = from;
	b.Transition.StateAfter = to;
	return b;
}

// Decodes velocity into pixels and, when `composite`, writes color with translucency composited over it.
// Constants come from the live config, so an ini change needs no rebuild.
void record_decode(ID3D12GraphicsCommandList *cl, ID3D12Resource *velocity, ID3D12Resource *color, ID3D12Resource *translucency, bool composite,
	float scale_x, float scale_y, bool mv_gamma, uint32_t min_x, uint32_t min_y, uint32_t w, uint32_t h)
{
	const uint32_t slot = g_native.ring++ % kDecodeRing;
	D3D12_CPU_DESCRIPTOR_HANDLE cpu = g_native.heap->GetCPUDescriptorHandleForHeapStart();
	D3D12_GPU_DESCRIPTOR_HANDLE gpu = g_native.heap->GetGPUDescriptorHandleForHeapStart();
	cpu.ptr += static_cast<SIZE_T>(slot) * kDescriptorsPerSet * g_native.inc;
	gpu.ptr += static_cast<UINT64>(slot) * kDescriptorsPerSet * g_native.inc;

	auto make_srv = [&](ID3D12Resource *r, DXGI_FORMAT fmt) {
		D3D12_SHADER_RESOURCE_VIEW_DESC srv = {};
		srv.Format = fmt;
		srv.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
		srv.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
		srv.Texture2D.MipLevels = 1;
		g_native.dev->CreateShaderResourceView(r, &srv, cpu);
		cpu.ptr += g_native.inc;
	};
	auto make_uav = [&](ID3D12Resource *r, DXGI_FORMAT fmt) {
		D3D12_UNORDERED_ACCESS_VIEW_DESC uav = {};
		uav.Format = fmt;
		uav.ViewDimension = D3D12_UAV_DIMENSION_TEXTURE2D;
		g_native.dev->CreateUnorderedAccessView(r, nullptr, &uav, cpu);
		cpu.ptr += g_native.inc;
	};
	make_srv(velocity, DXGI_FORMAT_R16G16_UNORM);
	make_srv(color, DXGI_FORMAT_R16G16B16A16_FLOAT);
	make_srv(translucency, DXGI_FORMAT_R16G16B16A16_FLOAT); // color again when not compositing: a valid, unread descriptor
	make_uav(g_native.mv, DXGI_FORMAT_R16G16_FLOAT);
	make_uav(g_native.comp, DXGI_FORMAT_R16G16B16A16_FLOAT);

	// Only texels inside the input viewport are written; the scale already has DLSS's current-to-previous sign.
	struct { float sx, sy; uint32_t min_x, min_y, w, h, encoding, composite; } constants = {
		scale_x, scale_y, min_x, min_y, w, h, mv_gamma ? 1u : 0u, composite ? 1u : 0u };
	ID3D12DescriptorHeap *heaps[] = { g_native.heap };
	cl->SetDescriptorHeaps(1, heaps);
	cl->SetComputeRootSignature(g_native.rs);
	cl->SetPipelineState(g_native.pso);
	cl->SetComputeRoot32BitConstants(0, 8, &constants, 0);
	cl->SetComputeRootDescriptorTable(1, gpu);
	cl->Dispatch((w + 7) / 8, (h + 7) / 8, 1);
}

// Averages 2x2 blocks of the DLSS output u0 into TSR's half-size output u1 (see half_fill.hlsl). u0 is in the UAV state
// after the evaluate, so it is moved to NON_PIXEL_SHADER_RESOURCE for the read and back; u1 is the pass's UAV output.
void record_half_fill(ID3D12GraphicsCommandList *cl, const Tex &src, const Tex &dst)
{
	auto *src_res = reinterpret_cast<ID3D12Resource *>(src.res.handle);
	auto *dst_res = reinterpret_cast<ID3D12Resource *>(dst.res.handle);
	const uint32_t slot = g_native.ring++ % kDecodeRing;
	D3D12_CPU_DESCRIPTOR_HANDLE cpu = g_native.heap->GetCPUDescriptorHandleForHeapStart();
	D3D12_GPU_DESCRIPTOR_HANDLE gpu = g_native.heap->GetGPUDescriptorHandleForHeapStart();
	cpu.ptr += static_cast<SIZE_T>(slot) * kDescriptorsPerSet * g_native.inc;
	gpu.ptr += static_cast<UINT64>(slot) * kDescriptorsPerSet * g_native.inc;

	D3D12_SHADER_RESOURCE_VIEW_DESC srv = {};
	srv.Format = DXGI_FORMAT_R11G11B10_FLOAT;
	srv.ViewDimension = D3D12_SRV_DIMENSION_TEXTURE2D;
	srv.Shader4ComponentMapping = D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING;
	srv.Texture2D.MipLevels = 1;
	g_native.dev->CreateShaderResourceView(src_res, &srv, cpu);
	D3D12_UNORDERED_ACCESS_VIEW_DESC uav = {};
	uav.Format = DXGI_FORMAT_R11G11B10_FLOAT;
	uav.ViewDimension = D3D12_UAV_DIMENSION_TEXTURE2D;
	cpu.ptr += g_native.inc;
	g_native.dev->CreateUnorderedAccessView(dst_res, nullptr, &uav, cpu);

	D3D12_RESOURCE_BARRIER before[2] = {
		transition(src_res, D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE),
		transition(dst_res, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_UNORDERED_ACCESS),
	};
	D3D12_RESOURCE_BARRIER after[2] = {
		transition(src_res, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_UNORDERED_ACCESS),
		transition(dst_res, D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE),
	};
	// u1 is a UAV descriptor in the game's table, so it already sits in the UAV state and only u0 changes.
	const UINT n = dst.uav_view ? 1 : 2;
	cl->ResourceBarrier(n, before);

	const uint32_t constants[4] = { dst.width, dst.height, src.width, src.height };
	ID3D12DescriptorHeap *heaps[] = { g_native.heap };
	cl->SetDescriptorHeaps(1, heaps);
	cl->SetComputeRootSignature(g_native.half_rs);
	cl->SetPipelineState(g_native.half_pso);
	cl->SetComputeRoot32BitConstants(0, 4, constants, 0);
	cl->SetComputeRootDescriptorTable(1, gpu);
	cl->Dispatch((dst.width + 7) / 8, (dst.height + 7) / 8, 1);

	D3D12_RESOURCE_BARRIER uav_barrier = {};
	uav_barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_UAV;
	uav_barrier.UAV.pResource = dst_res;
	cl->ResourceBarrier(1, &uav_barrier);
	cl->ResourceBarrier(n, after);
}

NVSDK_NGX_PerfQuality_Value ngx_quality(logic::Preset p)
{
	switch (p)
	{
	case logic::Preset::dlaa: return NVSDK_NGX_PerfQuality_Value_DLAA;
	case logic::Preset::quality: return NVSDK_NGX_PerfQuality_Value_MaxQuality;
	case logic::Preset::balanced: return NVSDK_NGX_PerfQuality_Value_Balanced;
	case logic::Preset::performance: return NVSDK_NGX_PerfQuality_Value_MaxPerf;
	default: return NVSDK_NGX_PerfQuality_Value_UltraPerformance;
	}
}

bool ensure_ngx(ID3D12Device *dev)
{
	if (g_ngx.init_tried)
		return g_ngx.ok;
	g_ngx.init_tried = true;

	std::wstring dir(g_dir.begin(), g_dir.end());
	std::wstring data = dir + L"ngx";
	CreateDirectoryW(data.c_str(), nullptr);
	const wchar_t *paths[] = { dir.c_str() };

	NVSDK_NGX_FeatureCommonInfo info = {};
	info.PathListInfo.Path = paths;
	info.PathListInfo.Length = 1;
	info.LoggingInfo.LoggingCallback = ngx_log;
	info.LoggingInfo.MinimumLoggingLevel = NVSDK_NGX_LOGGING_LEVEL_VERBOSE;
	info.LoggingInfo.DisableOtherLoggingSinks = false;

	NVSDK_NGX_Result r = NVSDK_NGX_D3D12_Init_with_ProjectID(kProjectId, NVSDK_NGX_ENGINE_TYPE_CUSTOM, "1.0", data.c_str(), dev, &info, NVSDK_NGX_Version_API);
	log_line("NVSDK_NGX_D3D12_Init_with_ProjectID: 0x%08x %s (NGX logs in %sngx, project id %s)", static_cast<unsigned>(r), narrow(GetNGXResultAsString(r)).c_str(), g_dir.c_str(), kProjectId);
	if (NVSDK_NGX_FAILED(r))
		return false;

	NVSDK_NGX_Parameter *caps = nullptr;
	r = NVSDK_NGX_D3D12_GetCapabilityParameters(&caps);
	int available = 0, needs_driver = 0;
	if (NVSDK_NGX_SUCCEED(r) && caps != nullptr)
	{
		NVSDK_NGX_Parameter_GetI(caps, NVSDK_NGX_Parameter_SuperSampling_Available, &available);
		NVSDK_NGX_Parameter_GetI(caps, NVSDK_NGX_Parameter_SuperSampling_NeedsUpdatedDriver, &needs_driver);
	}
	log_line("DLSS super resolution available=%d needs_updated_driver=%d (capability query 0x%08x)", available, needs_driver, static_cast<unsigned>(r));
	if (!available)
		return false;
	g_ngx.ok = true;
	return true;
}

// Creates the DLSS feature for these sizes and settings on the game's command list; the old one is parked, not released.
bool ensure_feature(ID3D12GraphicsCommandList *cl, uint32_t rw, uint32_t rh, uint32_t ow, uint32_t oh, bool output_subrects, const Config &cfg, bool &reset)
{
	if (g_ngx.feature != nullptr && g_ngx.rw == rw && g_ngx.rh == rh && g_ngx.ow == ow && g_ngx.oh == oh && g_ngx.output_subrects == output_subrects &&
		g_ngx.render_preset == cfg.render_preset && g_ngx.auto_exposure == cfg.auto_exposure)
		return true;
	if (g_ngx.feature != nullptr)
		g_ngx.retired.push_back(g_ngx.feature);
	g_ngx.feature = nullptr;
	if (g_ngx.params == nullptr)
	{
		const NVSDK_NGX_Result a = NVSDK_NGX_D3D12_AllocateParameters(&g_ngx.params);
		if (NVSDK_NGX_FAILED(a) || g_ngx.params == nullptr)
		{
			log_line("NVSDK_NGX_D3D12_AllocateParameters: 0x%08x %s", static_cast<unsigned>(a), narrow(GetNGXResultAsString(a)).c_str());
			return false;
		}
	}
	const logic::Preset preset = logic::preset_for_ratio(static_cast<double>(rw) / ow);
	NVSDK_NGX_DLSS_Create_Params create = {};
	create.Feature.InWidth = rw;
	create.Feature.InHeight = rh;
	create.Feature.InTargetWidth = ow;
	create.Feature.InTargetHeight = oh;
	create.Feature.InPerfQualityValue = ngx_quality(preset);
	create.InFeatureCreateFlags = NVSDK_NGX_DLSS_Feature_Flags_IsHDR | NVSDK_NGX_DLSS_Feature_Flags_MVLowRes | NVSDK_NGX_DLSS_Feature_Flags_DepthInverted |
		(cfg.auto_exposure ? NVSDK_NGX_DLSS_Feature_Flags_AutoExposure : 0);
	create.InEnableOutputSubrects = output_subrects;
	// One render preset for every mode: the five per-mode hints all get the same value (0 = Default, the SDK picks).
	for (const char *hint : { NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_DLAA, NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_Quality, NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_Balanced,
		NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_Performance, NVSDK_NGX_Parameter_DLSS_Hint_Render_Preset_UltraPerformance })
		NVSDK_NGX_Parameter_SetUI(g_ngx.params, hint, static_cast<unsigned>(cfg.render_preset));
	const NVSDK_NGX_Result r = NGX_D3D12_CREATE_DLSS_EXT(cl, 1, 1, &g_ngx.feature, g_ngx.params, &create);
	log_line("create DLSS feature: render viewport %ux%u -> output viewport %ux%u (output subrects %d), ratio %.3f, mode %s, render preset %s, flags IsHDR|MVLowRes|DepthInverted%s: 0x%08x %s",
		rw, rh, ow, oh, output_subrects, static_cast<double>(rw) / ow, logic::preset_name(preset), logic::render_preset_name(cfg.render_preset),
		cfg.auto_exposure ? "|AutoExposure" : "", static_cast<unsigned>(r), narrow(GetNGXResultAsString(r)).c_str());
	if (NVSDK_NGX_FAILED(r) || g_ngx.feature == nullptr)
	{
		g_ngx.feature = nullptr;
		return false;
	}
	g_ngx.rw = rw;
	g_ngx.rh = rh;
	g_ngx.ow = ow;
	g_ngx.oh = oh;
	g_ngx.output_subrects = output_subrects;
	g_ngx.render_preset = cfg.render_preset;
	g_ngx.auto_exposure = cfg.auto_exposure;
	reset = true;
	return true;
}

// Sets back everything the decode pass and NGX changed on the list, from the shadow taken before they ran.
struct Snapshot
{
	CmdState st;
	std::vector<LayoutParam> params;
	Bindings b;
	float jitter_x = 0, jitter_y = 0;
	logic::TsrConstants tsr;
	bool jitter_fallback = false;
	Config cfg; // g_cfg as it was when this dispatch started; the overlay may change g_cfg while the pass records
};

void restore_state(ID3D12GraphicsCommandList *cl, const Snapshot &s)
{
	ID3D12DescriptorHeap *heaps[2];
	UINT n = 0;
	if (s.st.heap_view != 0)
		heaps[n++] = reinterpret_cast<ID3D12DescriptorHeap *>(s.st.heap_view);
	if (s.st.heap_sampler != 0)
		heaps[n++] = reinterpret_cast<ID3D12DescriptorHeap *>(s.st.heap_sampler);
	if (n != 0)
		cl->SetDescriptorHeaps(n, heaps);
	if (s.st.layout == 0)
		return;
	cl->SetComputeRootSignature(reinterpret_cast<ID3D12RootSignature *>(s.st.layout));
	if (s.st.pipeline != 0)
		cl->SetPipelineState(reinterpret_cast<ID3D12PipelineState *>(s.st.pipeline));
	for (uint32_t p = 0; p < s.params.size(); ++p)
	{
		const LayoutParam &lp = s.params[p];
		switch (lp.type)
		{
		case pipeline_layout_param_type::descriptor_table:
		case pipeline_layout_param_type::descriptor_table_with_flags:
			if (const auto t = s.st.tables.find(p); t != s.st.tables.end())
				cl->SetComputeRootDescriptorTable(p, { t->second.handle });
			break;
		case pipeline_layout_param_type::push_constants:
			if (const auto c = s.st.consts.find(p); c != s.st.consts.end())
				cl->SetComputeRoot32BitConstants(p, static_cast<UINT>(c->second.size()), c->second.data(), 0);
			break;
		case pipeline_layout_param_type::push_descriptors:
		{
			const auto e = s.st.pushes.find(p);
			if (e == s.st.pushes.end() || e->second.empty() || lp.ranges.empty())
				break;
			const Info *info = e->second[0].info.get();
			if (info == nullptr || !info->buffer_range || info->resource == 0)
			{
				if (first_time(("restore-root-" + std::to_string(p)).c_str()))
					log_line("cannot restore root descriptor %u (not a buffer range); later passes may misrender", p);
				break;
			}
			const D3D12_GPU_VIRTUAL_ADDRESS va = reinterpret_cast<ID3D12Resource *>(info->resource)->GetGPUVirtualAddress() + info->offset;
			switch (lp.ranges[0].type)
			{
			case descriptor_type::constant_buffer: cl->SetComputeRootConstantBufferView(p, va); break;
			case descriptor_type::shader_resource_view:
			case descriptor_type::buffer_shader_resource_view: cl->SetComputeRootShaderResourceView(p, va); break;
			default: cl->SetComputeRootUnorderedAccessView(p, va); break;
			}
			break;
		}
		default:
			break;
		}
	}
}

// ---- ini (read at start, and again whenever its modification time changes) -------------------------------------------

bool ini_mtime(FILETIME &out)
{
	WIN32_FILE_ATTRIBUTE_DATA d;
	if (!GetFileAttributesExA(g_ini_path.c_str(), GetFileExInfoStandard, &d))
		return false;
	out = d.ftLastWriteTime;
	return true;
}

std::string config_summary(const Config &c)
{
	char buf[640];
	snprintf(buf, sizeof(buf),
		"enabled=%d render_preset=%s auto_exposure=%d jitter_sign=(%g,%g) mv_scale=(%g,%g) mv_scale_source=%s mv_encoding=%s composite_translucency=%d fill_half_res=%d never_fallback_after_start=%d log_every_n_frames=%u jitter_cycle=%u",
		c.enabled, logic::render_preset_name(c.render_preset), c.auto_exposure, c.jitter_sign_x, c.jitter_sign_y, c.mv_scale_x, c.mv_scale_y, c.mv_scale_from_game ? "game" : "ini",
		c.mv_gamma ? "gamma" : "linear", c.composite_translucency, c.fill_half_res, c.never_fallback_after_start, c.log_every, c.jitter_cycle);
	return buf;
}

std::string read_ini_text()
{
	std::string text;
	if (FILE *f = fopen(g_ini_path.c_str(), "rb"))
	{
		char buf[4096];
		size_t got;
		while ((got = fread(buf, 1, sizeof(buf), f)) > 0)
			text.append(buf, got);
		fclose(f);
	}
	return text;
}

void load_ini(bool reload)
{
	const std::string before = config_summary(g_cfg);
	const std::string text = read_ini_text();
	ini_mtime(g_ini_time);
	const logic::Ini ini = logic::parse_ini(text);
	g_cfg.enabled = ini.number("enabled", 1) != 0;
	g_cfg.jitter_sign_x = static_cast<float>(ini.number("jitter_sign_x", 1));
	g_cfg.jitter_sign_y = static_cast<float>(ini.number("jitter_sign_y", 1));
	g_cfg.mv_scale_x = static_cast<float>(ini.number("mv_scale_x", -0.5));
	g_cfg.mv_scale_y = static_cast<float>(ini.number("mv_scale_y", 0.5));
	g_cfg.log_every = static_cast<uint32_t>(ini.number("log_every_n_frames", 300));
	g_cfg.jitter_cycle = static_cast<uint32_t>(ini.number("jitter_cycle", 8));
	if (g_cfg.jitter_cycle == 0)
		g_cfg.jitter_cycle = 8;
	g_cfg.fill_half_res = ini.number("fill_half_res", 1) != 0;
	g_cfg.never_fallback_after_start = ini.number("never_fallback_after_start", 1) != 0;
	g_cfg.composite_translucency = ini.number("composite_translucency", 1) != 0;
	const std::string source = ini.text("mv_scale_source", "game");
	g_cfg.mv_scale_from_game = source != "ini";
	if (source != "ini" && source != "game")
		log_line("mv_scale_source=%s is not game or ini; using game", source.c_str());
	const std::string encoding = ini.text("mv_encoding", "gamma");
	g_cfg.mv_gamma = encoding == "gamma";
	if (encoding != "gamma" && encoding != "linear")
		log_line("mv_encoding=%s is not linear or gamma; using linear", encoding.c_str());
	const std::string preset = ini.text("render_preset", "default");
	g_cfg.render_preset = logic::parse_render_preset(preset);
	if (g_cfg.render_preset < 0)
	{
		log_line("render_preset=%s is not default, J, K, L or M; using default", preset.c_str());
		g_cfg.render_preset = 0;
	}
	g_cfg.auto_exposure = ini.number("auto_exposure", 1) != 0;

	const std::string after = config_summary(g_cfg);
	if (!reload)
		log_line("loaded: %s", after.c_str());
	else if (after != before)
	{
		log_line("ini changed, reloaded: %s (DLSS resets its history on the next frame)", after.c_str());
		g_reset_next = true;
	}
}

// Every 60 upscale dispatches: re-read the ini if its modification time changed.
void reload_ini_if_changed()
{
	static uint32_t tick = 0;
	FILETIME ft;
	if (++tick % 60 == 0 && ini_mtime(ft) && CompareFileTime(&ft, &g_ini_time) != 0)
		load_ini(true);
}

// The keys and values load_ini reads for `c`.
std::map<std::string, std::string> ini_values(const Config &c)
{
	const auto flag = [](bool v) { return std::string(v ? "1" : "0"); };
	return {
		{ "enabled", flag(c.enabled) },
		{ "render_preset", logic::render_preset_name(c.render_preset) },
		{ "auto_exposure", flag(c.auto_exposure) },
		{ "jitter_sign_x", str_format("%g", c.jitter_sign_x) },
		{ "jitter_sign_y", str_format("%g", c.jitter_sign_y) },
		{ "mv_scale_source", c.mv_scale_from_game ? "game" : "ini" },
		{ "mv_scale_x", str_format("%g", c.mv_scale_x) },
		{ "mv_scale_y", str_format("%g", c.mv_scale_y) },
		{ "log_every_n_frames", std::to_string(c.log_every) },
		{ "jitter_cycle", std::to_string(c.jitter_cycle) },
		{ "fill_half_res", flag(c.fill_half_res) },
		{ "never_fallback_after_start", flag(c.never_fallback_after_start) },
		{ "composite_translucency", flag(c.composite_translucency) },
		{ "mv_encoding", c.mv_gamma ? "gamma" : "linear" },
	};
}

// Overlay edits take effect at once (g_cfg is only written under g_mutex, and the dispatch path copies it under the same
// lock) and are then written to the ini. The file goes to a temp name and is moved over the ini, so the live reload never
// reads half a file; its mtime is recorded so the reload does not see our own write as a change. Returns false when the
// ini could not be written (the change is still live).
bool commit_config(Config c)
{
	c.jitter_cycle = c.jitter_cycle == 0 ? 1 : c.jitter_cycle;
	const std::string after = config_summary(c);
	{
		std::lock_guard<std::mutex> lock(g_mutex);
		if (config_summary(g_cfg) == after)
			return true;
		g_cfg = c;
		g_reset_next = true;
	}
	log_line("overlay changed: %s (DLSS resets its history on the next frame)", after.c_str());

	const std::string tmp = g_ini_path + ".tmp";
	const std::string text = logic::merge_ini(read_ini_text(), ini_values(c));
	FILE *f = fopen(tmp.c_str(), "wb");
	bool ok = f != nullptr && fwrite(text.data(), 1, text.size(), f) == text.size();
	if (f != nullptr)
		ok = fclose(f) == 0 && ok;
	ok = ok && MoveFileExA(tmp.c_str(), g_ini_path.c_str(), MOVEFILE_REPLACE_EXISTING);
	if (!ok)
	{
		DeleteFileA(tmp.c_str());
		log_line("the ini %s could not be written; the change is live but not saved", g_ini_path.c_str());
		return false;
	}
	std::lock_guard<std::mutex> lock(g_mutex);
	ini_mtime(g_ini_time);
	return true;
}

// ---- the hook -----------------------------------------------------------------------------------------------

// Counts since the last report, so a reason that keeps firing is visible even after its first three detailed lines.
struct Stats
{
	std::mutex m;
	uint64_t frames = 0;
	std::map<std::string, uint64_t> counts;
	std::string last_mismatch;
} g_stats;

void stat_count(const char *key)
{
	std::lock_guard<std::mutex> lock(g_stats.m);
	++g_stats.counts[key];
}
void stat_mismatch(const std::string &sizes)
{
	std::lock_guard<std::mutex> lock(g_stats.m);
	g_stats.last_mismatch = sizes;
}
// Called once per upscale dispatch; every log_every_n_frames of them it reports and starts over.
void stat_frame()
{
	std::lock_guard<std::mutex> lock(g_stats.m);
	if (++g_stats.frames < g_cfg.log_every || g_cfg.log_every == 0)
		return;
	std::string line = "accounting over " + std::to_string(g_stats.frames) + " upscale dispatches:";
	for (const auto &[key, n] : g_stats.counts)
		line += " " + key + "=" + std::to_string(n);
	if (!g_stats.last_mismatch.empty())
		line += " | latest size mismatch: " + g_stats.last_mismatch;
	log_line("%s", line.c_str());
	g_stats.frames = 0;
	g_stats.counts.clear();
	g_stats.last_mismatch.clear();
}

// What the last upscale dispatch did, for the overlay. Has its own mutex: the dispatch path writes it, the overlay reads it,
// and neither holds it while doing anything else.
struct Status
{
	enum class Path { none, dlss, fallback, disabled, kept_previous } path = Path::none;
	std::string reason; // the fallback key
	uint32_t rw = 0, rh = 0, ow = 0, oh = 0;
	logic::Preset mode = logic::Preset::dlaa;
	int render_preset = 0;
	bool auto_exposure = true;
	uint64_t evals = 0;
	std::string eval_result = "not evaluated yet";
	uint64_t seq = 0; // upscale dispatches seen; the overlay compares it between its own frames
} g_status;
std::mutex g_status_m;

void status_path(Status::Path path, const char *reason = "")
{
	std::lock_guard<std::mutex> lock(g_status_m);
	g_status.path = path;
	g_status.reason = reason;
	++g_status.seq;
}

// The first three times per reason are logged in full; every one is counted. A fallback means TSR runs, so DLSS
// resets its history on its next frame.
bool give_up(const char *key, const std::string &why)
{
	status_path(Status::Path::fallback, key);
	stat_count((std::string("fallback-") + key).c_str());
	if (g_ngx.evals > 0)
		g_reset_next = true;
	if (first_n(key, 3))
		log_line("not replacing the upscale pass: %s (the game renders normally)", why.c_str());
	return false;
}

// ReShade proxies the D3D12 device and command list. NGX has to see the proxies: they convert descriptor handles that
// the real objects (what get_native() returns) do not, and NGX's NVAPI calls pass through ReShade's dxgi.
// Proxy device: ReShade stores a pointer to it as private data on the real device.
const GUID kReShadeProxyDeviceId = { 0x2523AFF4, 0x978B, 0x4939, { 0xBA, 0x16, 0x8E, 0xE8, 0x76, 0xA4, 0xCB, 0x2A } };

struct ProxyDevice
{
	bool checked = false, rule_confirmed = false;
	ID3D12Device *device = nullptr;
} g_proxy;

bool proxy_device(device *dev, ID3D12Device *&out, std::string &why)
{
	if (!g_proxy.checked)
	{
		g_proxy.checked = true;
		auto *real = reinterpret_cast<ID3D12Device *>(dev->get_native());
		void *stored = nullptr;
		UINT size = sizeof(stored);
		const HRESULT hr = real->GetPrivateData(kReShadeProxyDeviceId, &size, &stored);
		// The proxy derives from the device interface (one vptr, 8 bytes) and then from the add-on API object.
		void *guess = reinterpret_cast<char *>(dev) - 8;
		g_proxy.rule_confirmed = SUCCEEDED(hr) && stored != nullptr && stored == guess;
		if (SUCCEEDED(hr) && stored != nullptr)
			g_proxy.device = static_cast<ID3D12Device *>(stored);
		log_line("ReShade proxy device: private data %p (hr 0x%08x), api object minus 8 = %p, %s; real device %p", stored, static_cast<unsigned>(hr), guess,
			g_proxy.rule_confirmed ? "both agree, the layout rule holds" : "they DISAGREE, the proxy command list cannot be derived", static_cast<void *>(real));
	}
	out = g_proxy.device;
	if (out == nullptr)
		why = "no ReShade proxy device is registered on the real device";
	return out != nullptr;
}

// No private-data link exists for the list. Only trusted when the device rule held, and when the candidate answers a
// QueryInterface for ID3D12GraphicsCommandList with itself, as a ReShade proxy does.
bool proxy_list(command_list *cmd_list, ID3D12GraphicsCommandList *&out, std::string &why)
{
	if (!g_proxy.rule_confirmed)
	{
		why = "the proxy device layout rule was not confirmed, so the proxy command list cannot be derived";
		return false;
	}
	auto *candidate = reinterpret_cast<ID3D12GraphicsCommandList *>(reinterpret_cast<char *>(cmd_list) - 8);
	void *answer = nullptr;
	const HRESULT hr = candidate->QueryInterface(__uuidof(ID3D12GraphicsCommandList), &answer);
	if (answer != nullptr)
		static_cast<IUnknown *>(answer)->Release();
	if (FAILED(hr) || answer != static_cast<void *>(candidate))
	{
		why = "the derived proxy command list did not answer QueryInterface with itself";
		return false;
	}
	out = candidate;
	return true;
}

bool replace_upscale(command_list *cmd_list, Snapshot &s)
{
	device *dev = cmd_list->get_device();
	auto *native_cl = reinterpret_cast<ID3D12GraphicsCommandList *>(cmd_list->get_native());
	const Bindings &b = s.b;
	const Config &cfg = s.cfg;

	std::string why;
	ID3D12Device *pdev = nullptr;
	ID3D12GraphicsCommandList *cl = nullptr; // the proxy list; native_cl only restores state
	if (!proxy_device(dev, pdev, why) || !proxy_list(cmd_list, cl, why))
		return give_up("proxy", why);

	if (b.output.width < b.color.width || b.output.height < b.color.height)
		return give_up("output-smaller", "output is smaller than the render size: " + describe("u0 output", b.output) + "; " + describe("u3 color", b.color));
	if (b.velocity.res_format != format::r16g16_unorm && b.velocity.res_format != format::r16g16_typeless)
		return give_up("velocity-format", "u6 is not r16g16_unorm: " + describe("u6 velocity", b.velocity));
	if (!ensure_native(pdev))
		return give_up("native", "the decode pass could not be created");
	if (!ensure_ngx(pdev))
		return give_up("ngx", "NGX or DLSS is unavailable (see the NGX lines above)");
	if (!ensure_mv_texture(b.color.width, b.color.height))
		return give_up("mv-texture", "motion vector texture creation failed");

	// Translucency is composited over the scene color only when both are plain r16g16b16a16_float of the same size.
	auto is_rgba16f = [](format f) { return f == format::r16g16b16a16_float || f == format::r16g16b16a16_typeless; };
	bool composite = cfg.composite_translucency;
	if (composite && !(b.translucency.ok && is_rgba16f(b.translucency.res_format) && is_rgba16f(b.color.res_format) &&
		b.translucency.width == b.color.width && b.translucency.height == b.color.height))
	{
		if (first_n("composite-skipped", 3))
			log_line("translucency composite skipped, DLSS gets u3 raw: %s; %s", describe("u3 color", b.color).c_str(), describe("u4 translucency", b.translucency).c_str());
		stat_count("composite-skipped");
		composite = false;
	}
	if (composite && !ensure_comp_texture(b.color.width, b.color.height))
		composite = false;

	if (first_time("known-gaps"))
		log_line("known gaps: translucency u4 (%s); u2 (history metadata) is no longer written; u1 (half-size color) is %s",
			composite ? "composited over u3 before DLSS" : "dropped", cfg.fill_half_res ? "filled from u0" : "no longer written (fill_half_res=0)");

	// DLSS works on the viewports TSR itself uses, not on the texture extents: below the viewport the texture holds garbage.
	const logic::TsrConstants &t = s.tsr;
	const uint32_t rw = static_cast<uint32_t>(t.input_size[0] + 0.5f), rh = static_cast<uint32_t>(t.input_size[1] + 0.5f);
	const uint32_t ow = static_cast<uint32_t>(t.history_size[0] + 0.5f), oh = static_cast<uint32_t>(t.history_size[1] + 0.5f);
	if (t.input_min[0] + rw > b.color.width || t.input_min[1] + rh > b.color.height || t.history_min[0] + ow > b.output.width || t.history_min[1] + oh > b.output.height)
		return give_up("viewport", str_format("a TSR viewport lies outside its texture: input min (%u,%u) size %ux%u in color %ux%u, history min (%u,%u) size %ux%u in output %ux%u",
			t.input_min[0], t.input_min[1], rw, rh, b.color.width, b.color.height, t.history_min[0], t.history_min[1], ow, oh, b.output.width, b.output.height));
	const bool output_subrects = t.history_min[0] != 0 || t.history_min[1] != 0;

	// Velocity to pixels: the game's own scale (b0 @272, UE: ScreenVelocityToInputPixelVelocity), negated because DLSS
	// wants current to previous and UE's V is current minus previous; or the ini factors times the viewport size.
	const float scale_x = cfg.mv_scale_from_game ? -t.velocity_to_pixels[0] : cfg.mv_scale_x * rw;
	const float scale_y = cfg.mv_scale_from_game ? -t.velocity_to_pixels[1] : cfg.mv_scale_y * rh;
	{
		static uint32_t last[12] = {};
		const uint32_t now[12] = { t.input_min[0], t.input_min[1], rw, rh, t.history_min[0], t.history_min[1], ow, oh,
			static_cast<uint32_t>(static_cast<int32_t>(scale_x * 16)), static_cast<uint32_t>(static_cast<int32_t>(scale_y * 16)), cfg.mv_scale_from_game ? 1u : 0u, 0u };
		if (memcmp(last, now, sizeof(now)) != 0)
		{
			memcpy(last, now, sizeof(now));
			log_line("TSR viewports: input min (%u,%u) size %ux%u of color %ux%u; history min (%u,%u) size %ux%u of output %ux%u; velocity to pixels (%g, %g) from %s "
				"(game value (%g, %g)), %s encoding", t.input_min[0], t.input_min[1], rw, rh, b.color.width, b.color.height, t.history_min[0], t.history_min[1], ow, oh,
				b.output.width, b.output.height, scale_x, scale_y, cfg.mv_scale_from_game ? "the game" : "the ini", t.velocity_to_pixels[0], t.velocity_to_pixels[1],
				cfg.mv_gamma ? "gamma" : "linear");
		}
	}

	bool reset = g_ngx.evals == 0 || g_reset_next;
	g_reset_next = false;
	if (t.camera_cut != 0)
	{
		reset = true;
		stat_count("camera-cut");
	}

	auto *color = reinterpret_cast<ID3D12Resource *>(b.color.res.handle);
	auto *translucency = reinterpret_cast<ID3D12Resource *>(b.translucency.res.handle);
	auto *depth = reinterpret_cast<ID3D12Resource *>(b.depth.res.handle);
	auto *velocity = reinterpret_cast<ID3D12Resource *>(b.velocity.res.handle);
	auto *output = reinterpret_cast<ID3D12Resource *>(b.output.res.handle);

	// Inputs the game left in the UAV state must be readable (NON_PIXEL_SHADER_RESOURCE) for the decode pass and DLSS.
	std::vector<D3D12_RESOURCE_BARRIER> before, after;
	std::vector<const Tex *> inputs = { &b.color, &b.depth, &b.velocity };
	if (composite)
		inputs.push_back(&b.translucency);
	for (const Tex *t : inputs)
		if (t->uav_view)
		{
			auto *r = reinterpret_cast<ID3D12Resource *>(t->res.handle);
			before.push_back(transition(r, D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE));
			after.push_back(transition(r, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_UNORDERED_ACCESS));
		}
	if (!before.empty())
		cl->ResourceBarrier(static_cast<UINT>(before.size()), before.data());

	record_decode(cl, velocity, color, composite ? translucency : color, composite, scale_x, scale_y, cfg.mv_gamma, t.input_min[0], t.input_min[1], rw, rh);
	// The decode pass wrote the motion vectors (and the composited color): make them readable for DLSS.
	std::vector<D3D12_RESOURCE_BARRIER> generated;
	std::vector<ID3D12Resource *> generated_res = { g_native.mv };
	if (composite)
		generated_res.push_back(g_native.comp);
	for (ID3D12Resource *g : generated_res)
	{
		D3D12_RESOURCE_BARRIER u = {};
		u.Type = D3D12_RESOURCE_BARRIER_TYPE_UAV;
		u.UAV.pResource = g;
		generated.push_back(u);
	}
	for (ID3D12Resource *g : generated_res)
		generated.push_back(transition(g, D3D12_RESOURCE_STATE_UNORDERED_ACCESS, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE));
	cl->ResourceBarrier(static_cast<UINT>(generated.size()), generated.data());

	bool ok = ensure_feature(cl, rw, rh, ow, oh, output_subrects, cfg, reset);
	const bool feature_ok = ok;
	if (!ok)
		g_ngx.ok = false; // stop retrying (and logging) every frame; the original pass runs
	NVSDK_NGX_Result r = NVSDK_NGX_Result_Fail;
	if (ok)
	{
		NVSDK_NGX_D3D12_DLSS_Eval_Params e = {};
		e.Feature.pInColor = composite ? g_native.comp : color;
		e.Feature.pInOutput = output;
		e.pInDepth = depth;
		e.pInMotionVectors = g_native.mv;
		e.InJitterOffsetX = s.jitter_x * cfg.jitter_sign_x;
		e.InJitterOffsetY = s.jitter_y * cfg.jitter_sign_y;
		e.InRenderSubrectDimensions = { rw, rh };
		// The decode pass and the composite write at the same texel coordinates as their sources, so every input shares the
		// input viewport origin; the output goes to the history viewport.
		e.InColorSubrectBase = { t.input_min[0], t.input_min[1] };
		e.InDepthSubrectBase = { t.input_min[0], t.input_min[1] };
		e.InMVSubrectBase = { t.input_min[0], t.input_min[1] };
		e.InOutputSubrectBase = { t.history_min[0], t.history_min[1] };
		e.InReset = reset ? 1 : 0;
		e.InMVScaleX = 1.0f;
		e.InMVScaleY = 1.0f;
		if (!cfg.auto_exposure) // no exposure texture is supplied: tell DLSS the exposure is neutral
			e.InPreExposure = e.InExposureScale = 1.0f;
		r = NGX_D3D12_EVALUATE_DLSS_EXT(cl, g_ngx.feature, g_ngx.params, &e);
		ok = NVSDK_NGX_SUCCEED(r);
		stat_count(ok ? "evaluate-ok" : "evaluate-failed");
		const uint64_t n = ++g_ngx.evals;
		if (!ok)
			g_ngx.ok = false; // a failing evaluate is logged once below and not retried
		if (n <= 10 || !ok || (cfg.log_every != 0 && n % cfg.log_every == 0))
			log_line("evaluate #%llu: 0x%08x %s viewport %ux%u -> %ux%u jitter (%+.4f, %+.4f)%s reset=%d%s", static_cast<unsigned long long>(n),
				static_cast<unsigned>(r), narrow(GetNGXResultAsString(r)).c_str(), rw, rh, ow, oh, s.jitter_x, s.jitter_y,
				s.jitter_fallback ? " [fallback B]" : " [read from b0]", reset ? 1 : 0, ok ? (t.camera_cut != 0 ? " (camera cut)" : "") : " FAILED, original pass runs");
	}
	{
		std::lock_guard<std::mutex> lock(g_status_m);
		g_status.path = ok ? Status::Path::dlss : Status::Path::fallback;
		g_status.reason = ok ? "" : feature_ok ? "evaluate" : "feature-create";
		++g_status.seq;
		g_status.rw = rw;
		g_status.rh = rh;
		g_status.ow = ow;
		g_status.oh = oh;
		g_status.mode = logic::preset_for_ratio(static_cast<double>(rw) / ow);
		g_status.render_preset = cfg.render_preset;
		g_status.auto_exposure = cfg.auto_exposure;
		if (feature_ok)
		{
			g_status.evals = g_ngx.evals;
			g_status.eval_result = str_format("0x%08x %s", static_cast<unsigned>(r), narrow(GetNGXResultAsString(r)).c_str());
		}
	}

	std::vector<D3D12_RESOURCE_BARRIER> back;
	for (ID3D12Resource *g : generated_res)
		back.push_back(transition(g, D3D12_RESOURCE_STATE_NON_PIXEL_SHADER_RESOURCE, D3D12_RESOURCE_STATE_UNORDERED_ACCESS));
	cl->ResourceBarrier(static_cast<UINT>(back.size()), back.data());
	// Only after a successful evaluate: when it failed the original pass still runs and writes u1 itself.
	if (ok && cfg.fill_half_res)
	{
		const Tex &half = b.half;
		if (!g_native.half_ready || !half.ok || half.res_format != format::r11g11b10_float || b.output.res_format != format::r11g11b10_float)
		{
			if (first_time("half-skipped"))
				log_line("half-res fill skipped: %s", g_native.half_ready ? (describe("u1", half) + "; " + describe("u0", b.output)).c_str() : "the fill shader is unavailable");
		}
		else
		{
			if (first_time("half-fill"))
				log_line("half-res fill: u1 %ux%u from u0 %ux%u", half.width, half.height, b.output.width, b.output.height);
			record_half_fill(cl, b.output, half);
		}
	}
	if (!after.empty())
		cl->ResourceBarrier(static_cast<UINT>(after.size()), after.data());
	// Everything the decode pass and NGX set is put back on the real list, with the real heap and root signature objects.
	restore_state(native_cl, s);
	return ok;
}

bool on_dispatch(command_list *cmd_list, uint32_t, uint32_t, uint32_t)
{
	if (t_busy)
		return false;
	Snapshot s;
	{
		std::lock_guard<std::mutex> lock(g_mutex);
		const auto it = g_cmds.find(cmd_list);
		if (it == g_cmds.end())
			return false;
		const auto pipe = g_pipelines.find(it->second.pipeline);
		if (pipe == g_pipelines.end() || pipe->second != kUpscaleHash)
			return false;

		stat_frame();
		reload_ini_if_changed();
		if (!g_cfg.enabled)
		{
			// Disabled live: TSR runs, so DLSS starts over with a reset when it is enabled again.
			if (g_ngx.evals > 0)
				g_reset_next = true;
			status_path(Status::Path::disabled);
			return false;
		}
		device *dev = cmd_list->get_device();
		s.st = it->second;
		s.cfg = g_cfg;
		if (const auto l = g_layouts.find(s.st.layout); l != g_layouts.end())
			s.params = l->second;
		std::string bind_why;
		if (!find_bindings(dev, s.st, s.b, bind_why))
			return give_up("bindings", bind_why + " [" + describe_all(s.b) + "]");

		Bindings &b = s.b;
		// This TSR pass reads no depth: u14 is an unrelated slot UE refills every frame (seen empty, 1x1, 5712x5712).
		// Depth is the latest depth buffer bound at render size; if there is none, the mismatch path below decides.
		find_depth_of_size(b.color.width, b.color.height, b.depth);
		if (b.color.width != b.velocity.width || b.color.height != b.velocity.height || b.color.width != b.depth.width || b.color.height != b.depth.height)
		{
			const std::string sizes = describe("u3 color", b.color) + "; " + describe("u6 velocity", b.velocity) + "; " + describe("depth", b.depth);
			stat_mismatch(sizes);
			Tex alt;
			const bool depth_only = b.color.width == b.velocity.width && b.color.height == b.velocity.height;
			if (depth_only && find_depth_of_size(b.color.width, b.color.height, alt))
			{
				stat_count("size-mismatch-depth-substituted");
				if (first_n("depth-substituted", 3))
					log_line("size mismatch, using the latest depth buffer of render size %ux%u instead of the bound one: %s; substitute: %s", b.color.width, b.color.height,
						sizes.c_str(), describe("depth", alt).c_str());
				b.depth = alt;
			}
			else if (g_cfg.never_fallback_after_start && g_ngx.evals > 0)
			{
				stat_count("size-mismatch-kept-previous-output");
				status_path(Status::Path::kept_previous);
				if (first_n("kept-previous", 3))
					log_line("size mismatch and no usable depth: skipping TSR without evaluating, u0 keeps the previous DLSS output: %s", sizes.c_str());
				return true;
			}
			else
				return give_up("size-mismatch", "color, velocity and depth differ in size and no depth of render size was seen: " + sizes);
		}

		std::string why;
		if (read_tsr_constants(dev, s.st, s.tsr, why))
		{
			g_tsr_last = s.tsr;
			g_tsr_last_valid = true;
			s.jitter_x = s.tsr.jitter[0];
			s.jitter_y = s.tsr.jitter[1];
			const int phase = logic::match_phase(s.jitter_x, s.jitter_y, g_cfg.jitter_cycle);
			if (phase >= 0)
			{
				g_jitter.phase = phase;
				g_jitter.phase_eval = g_ngx.evals;
			}
			g_jitter.using_fallback = false;
		}
		else if (g_jitter.phase >= 0 && g_tsr_last_valid)
		{
			s.tsr = g_tsr_last; // last viewport and velocity scale; no camera cut can be known
			s.tsr.camera_cut = 0;
			logic::predict_jitter(g_jitter.phase, static_cast<uint32_t>(g_ngx.evals - g_jitter.phase_eval), g_cfg.jitter_cycle, s.jitter_x, s.jitter_y);
			s.jitter_fallback = true;
			if (first_time("jitter-b"))
				log_line("TSR constants option A failed (%s); using option B, the Halton sequence locked from an earlier read and the last viewport and scale", why.c_str());
		}
		else
			return give_up("jitter", "the TSR constants could not be read (" + why + ") and no earlier read locked the Halton phase");
	}

	t_busy = true;
	const bool replaced = replace_upscale(cmd_list, s);
	t_busy = false;
	return replaced;
}

// ---- the "DLSS" tab of ReShade's overlay -----------------------------------------------------------------------------

// The overlay draws on another call path than the dispatch hooks. It only copies g_status and g_cfg under their own short
// locks and commits edits through commit_config, so the render path is never blocked for longer than a struct copy.
// Read-only status of the last upscale dispatch.
void draw_status(const Status &st, bool dispatching)
{
	switch (st.path)
	{
	case Status::Path::none: ImGui::Text("DLSS: no upscale pass seen yet"); break;
	case Status::Path::dlss: ImGui::Text("DLSS: active"); break;
	case Status::Path::fallback: ImGui::Text("DLSS: not active, the game's TSR ran (fallback: %s)", st.reason.c_str()); break;
	case Status::Path::disabled: ImGui::Text("DLSS: off (Enabled is unchecked), the game's TSR runs"); break;
	case Status::Path::kept_previous: ImGui::Text("DLSS: skipped this frame, the previous DLSS output was kept (no usable depth)"); break;
	}
	if (st.path != Status::Path::none && !dispatching)
		ImGui::TextDisabled("No upscale pass ran since the last overlay frame; this is the last one seen.");
	if (st.rw != 0)
		ImGui::Text("Render %ux%u -> output %ux%u, ratio %.3f", st.rw, st.rh, st.ow, st.oh, static_cast<double>(st.rw) / st.ow);
	if (st.rw != 0)
		ImGui::Text("DLSS mode: %s", logic::preset_name(st.mode));
	ImGui::Text("Last NGX evaluate: %s", st.eval_result.c_str());
	ImGui::Text("Evaluate count: %llu", static_cast<unsigned long long>(st.evals));
	if (st.rw != 0)
		ImGui::Text("Render preset in use: %s, auto exposure %s", logic::render_preset_name(st.render_preset), st.auto_exposure ? "on" : "off");
	ImGui::TextWrapped("Mode follows the game's Anti-Aliasing Level and resolution.");
}

void draw_overlay(reshade::api::effect_runtime *)
{
	static Config edit; // what the widgets show; refreshed from g_cfg whenever no widget is being edited
	static bool saved = true;
	static uint64_t last_seq = 0;

	Status st;
	{
		std::lock_guard<std::mutex> lock(g_status_m);
		st = g_status;
	}
	const bool dispatching = st.seq != last_seq;
	last_seq = st.seq;
	if (!ImGui::IsAnyItemActive())
	{
		std::lock_guard<std::mutex> lock(g_mutex);
		edit = g_cfg;
	}

	ImGui::SeparatorText("Status");
	draw_status(st, dispatching);

	// Checkboxes and combos apply on the click; typed numbers apply when the field is left (Enter or click away).
	bool changed = false;
	const auto typed = [&] { changed |= ImGui::IsItemDeactivatedAfterEdit(); };

	ImGui::SeparatorText("Controls");
	changed |= ImGui::Checkbox("Enabled", &edit.enabled);
	const char *names[logic::kRenderPresetCount];
	for (int i = 0; i < logic::kRenderPresetCount; ++i)
		names[i] = logic::kRenderPresets[i].name;
	int preset = logic::render_preset_index(edit.render_preset);
	if (ImGui::Combo("Render preset", &preset, names, logic::kRenderPresetCount))
	{
		edit.render_preset = logic::kRenderPresets[preset].value;
		changed = true;
	}
	changed |= ImGui::Checkbox("Auto exposure", &edit.auto_exposure);
	ImGui::TextWrapped("Render preset and auto exposure recreate the DLSS feature and reset its history. Default lets the SDK pick per mode; "
		"K is the transformer model at best quality; J has slightly less ghosting and more flicker; L and M are the SDK defaults for Ultra Performance and Performance.");
	ImGui::TextWrapped("Not offered: sharpness (deprecated in DLSS since 2.5.1, no effect), frame generation and Reflex (not possible from this add-on).");

	if (ImGui::CollapsingHeader("Advanced"))
	{
		ImGui::InputFloat("jitter_sign_x", &edit.jitter_sign_x, 0.0f, 0.0f, "%g");
		typed();
		ImGui::InputFloat("jitter_sign_y", &edit.jitter_sign_y, 0.0f, 0.0f, "%g");
		typed();
		int source = edit.mv_scale_from_game ? 0 : 1;
		if (ImGui::Combo("mv_scale_source", &source, "game\0ini\0"))
		{
			edit.mv_scale_from_game = source == 0;
			changed = true;
		}
		ImGui::BeginDisabled(edit.mv_scale_from_game); // only used with mv_scale_source=ini
		ImGui::InputFloat("mv_scale_x", &edit.mv_scale_x, 0.0f, 0.0f, "%g");
		typed();
		ImGui::InputFloat("mv_scale_y", &edit.mv_scale_y, 0.0f, 0.0f, "%g");
		typed();
		ImGui::EndDisabled();
		int encoding = edit.mv_gamma ? 0 : 1;
		if (ImGui::Combo("mv_encoding", &encoding, "gamma\0linear\0"))
		{
			edit.mv_gamma = encoding == 0;
			changed = true;
		}
		changed |= ImGui::Checkbox("composite_translucency", &edit.composite_translucency);
		changed |= ImGui::Checkbox("fill_half_res", &edit.fill_half_res);
		changed |= ImGui::Checkbox("never_fallback_after_start", &edit.never_fallback_after_start);
		ImGui::InputScalar("log_every_n_frames", ImGuiDataType_U32, &edit.log_every);
		typed();
		ImGui::InputScalar("jitter_cycle", ImGuiDataType_U32, &edit.jitter_cycle);
		typed();
	}

	if (changed)
		saved = commit_config(edit);
	if (!saved)
		ImGui::TextWrapped("The ini file could not be written: the changes are live but will be lost when the game restarts.");
}
} // namespace

extern "C"
{
__declspec(dllexport) const char *NAME = "AMDLSS DX12 DLSS";
__declspec(dllexport) const char *DESCRIPTION = "Replaces the game's TSR upscale pass with DLSS Super Resolution (spike, stage 3b).";
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
		g_dir.assign(path, n);
		g_dir.erase(g_dir.find_last_of("\\/") + 1);
		g_log_path = g_dir + "amdlss_dx12_dlss.log";

		g_ini_path = g_dir + "amdlss_dx12_dlss.ini";
		load_ini(false);

		reshade::register_overlay("DLSS", draw_overlay);

		reshade::register_event<addon_event::init_pipeline>(on_init_pipeline);
		reshade::register_event<addon_event::destroy_pipeline>(on_destroy_pipeline);
		reshade::register_event<addon_event::init_pipeline_layout>(on_init_pipeline_layout);
		reshade::register_event<addon_event::destroy_pipeline_layout>(on_destroy_pipeline_layout);
		reshade::register_event<addon_event::init_resource_view>(on_init_resource_view);
		reshade::register_event<addon_event::destroy_resource_view>(on_destroy_resource_view);
		reshade::register_event<addon_event::init_resource>(on_init_resource);
		reshade::register_event<addon_event::destroy_resource>(on_destroy_resource);
		reshade::register_event<addon_event::update_descriptor_tables>(on_update_descriptor_tables);
		reshade::register_event<addon_event::copy_descriptor_tables>(on_copy_descriptor_tables);
		reshade::register_event<addon_event::reset_command_list>(on_reset_command_list);
		reshade::register_event<addon_event::destroy_command_list>(on_reset_command_list);
		reshade::register_event<addon_event::bind_pipeline>(on_bind_pipeline);
		reshade::register_event<addon_event::push_descriptors>(on_push_descriptors);
		reshade::register_event<addon_event::push_constants>(on_push_constants);
		reshade::register_event<addon_event::bind_descriptor_tables>(on_bind_descriptor_tables);
		reshade::register_event<addon_event::bind_render_targets_and_depth_stencil>(on_bind_render_targets);
		reshade::register_event<addon_event::dispatch>(on_dispatch);
		break;
	}
	case DLL_PROCESS_DETACH:
		reshade::unregister_overlay("DLSS", draw_overlay);
		reshade::unregister_addon(module);
		break;
	}
	return TRUE;
}
