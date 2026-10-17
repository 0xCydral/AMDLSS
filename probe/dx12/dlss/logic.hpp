/*
 * Pure logic of the DLSS add-on (no Windows, no ReShade, no NGX), so selftest.cpp can check it on the Linux host
 * inside the build container: which TSR pass a shader hash is, TSR constant layouts, two-pass decisions, Halton jitter,
 * jitter phase matching, preset choice, UE5 velocity decode, ini parsing and merging, DLSS render preset names,
 * frame-time statistics.
 */
#pragma once

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>

namespace logic
{
inline double halton(uint32_t index, uint32_t base)
{
	double f = 1.0, r = 0.0;
	while (index > 0)
	{
		f /= base;
		r += f * (index % base);
		index /= base;
	}
	return r;
}

// UE's temporal jitter: Halton(2,3) - 0.5 render pixels. `index` is the 1-based Halton index.
inline void jitter_at(uint32_t index, float &x, float &y)
{
	x = static_cast<float>(halton(index, 2) - 0.5);
	y = static_cast<float>(halton(index, 3) - 0.5);
}

// The 0-based phase p in [0, cycle) whose jitter equals (x, y), or -1 when the pair is not on the sequence.
inline int match_phase(float x, float y, uint32_t cycle, float eps = 2e-3f)
{
	for (uint32_t p = 0; p < cycle; ++p)
	{
		float hx, hy;
		jitter_at(p + 1, hx, hy);
		if (std::fabs(hx - x) < eps && std::fabs(hy - y) < eps)
			return static_cast<int>(p);
	}
	return -1;
}

// Predicted jitter `frames_since_lock` evaluations after the pair at `phase` was read.
inline void predict_jitter(int phase, uint32_t frames_since_lock, uint32_t cycle, float &x, float &y)
{
	jitter_at((static_cast<uint32_t>(phase) + frames_since_lock) % cycle + 1, x, y);
}

// ---- which TSR pass a compute shader is ------------------------------------------------------------------------------
// FNV-1a hashes of the DXIL, found by the probe (odd/tasks/dx12-spike.md). With the history at 100% of the output TSR is one
// pass (UpdateHistory writes the output and the half-size target); with the history at 200% it is two: UpdateHistory writes
// the 5120x2880 history, a resolve reads it and writes the 2560x1440 output and the half-size target.
constexpr uint64_t kHashSinglePass = 0x1ff10c3ce95d3bcbull;
constexpr uint64_t kHashUpdateHistory = 0xe32f96d4dc79be5bull;
constexpr uint64_t kHashResolve = 0x0b1375003bfb8294ull;

enum class TsrPass { none, single, update_history, resolve };

inline TsrPass tsr_pass_for_hash(uint64_t hash)
{
	switch (hash)
	{
	case kHashSinglePass: return TsrPass::single;
	case kHashUpdateHistory: return TsrPass::update_history;
	case kHashResolve: return TsrPass::resolve;
	default: return TsrPass::none;
	}
}

// The fields of TSR's root constant buffer b0 (_RootShaderParameters) that the DLSS add-on needs. The default byte offsets
// come from the disassembly of UE's own single-pass shader (T8, tsr_1ff10c3ce95d3bcb.dxil).
constexpr size_t kTsrInputViewportMin = 32;      // uint2, then InputInfo_ViewportMax uint2 @40
constexpr size_t kTsrInputViewportSize = 48;     // float2
constexpr size_t kTsrHistoryViewportMin = 144;   // uint2
constexpr size_t kTsrHistoryViewportSize = 160;  // float2
constexpr size_t kTsrJitter = 256;               // float2, render pixels
constexpr size_t kTsrCameraCut = 264;            // uint
constexpr size_t kTsrVelocityToPixels = 272;     // float2, ScreenVelocityToInputPixelVelocity
constexpr size_t kTsrConstantsBytes = 280;       // bytes of b0 the add-on reads

// Where those fields sit in b0 for one shader. The 200% UpdateHistory shader (e32f96d4dc79be5b) has not been disassembled yet
// (its DXIL only exists at run time), so every hash uses the single-pass offsets; UE puts the common TSR parameters first in
// every TSR shader's parameter struct. If the live run reports implausible constants for it, this table is the one place to fix.
struct TsrLayout
{
	size_t input_min, input_size, history_min, history_size, jitter, camera_cut, velocity;
	size_t bytes() const { return velocity + 8; } // the last field is a float2
};
constexpr TsrLayout kTsrDefaultLayout = { kTsrInputViewportMin, kTsrInputViewportSize, kTsrHistoryViewportMin, kTsrHistoryViewportSize, kTsrJitter,
	kTsrCameraCut, kTsrVelocityToPixels };
static_assert(kTsrDefaultLayout.velocity + 8 == kTsrConstantsBytes, "kTsrConstantsBytes must cover the default layout");

inline TsrLayout tsr_layout_for_hash(uint64_t) { return kTsrDefaultLayout; }

struct TsrConstants
{
	uint32_t input_min[2] = {}, history_min[2] = {};
	float input_size[2] = {}, history_size[2] = {};
	float jitter[2] = {}, velocity_to_pixels[2] = {};
	uint32_t camera_cut = 0;
};

inline uint32_t read_u32(const uint8_t *p, size_t at)
{
	uint32_t v;
	std::memcpy(&v, p + at, sizeof(v));
	return v;
}
inline float read_f32(const uint8_t *p, size_t at)
{
	float v;
	std::memcpy(&v, p + at, sizeof(v));
	return v;
}

// Parses the fields above; false when the buffer is short or the values cannot be a real TSR frame (non-positive
// viewport, jitter outside +-0.5 pixels, non-finite velocity scale), so a mapped buffer holding something else is never used.
inline bool parse_tsr_constants(const uint8_t *buf, size_t size, TsrConstants &out, const TsrLayout &layout = kTsrDefaultLayout)
{
	if (buf == nullptr || size < layout.bytes())
		return false;
	TsrConstants c;
	for (int i = 0; i < 2; ++i)
	{
		c.input_min[i] = read_u32(buf, layout.input_min + 4 * i);
		c.history_min[i] = read_u32(buf, layout.history_min + 4 * i);
		c.input_size[i] = read_f32(buf, layout.input_size + 4 * i);
		c.history_size[i] = read_f32(buf, layout.history_size + 4 * i);
		c.jitter[i] = read_f32(buf, layout.jitter + 4 * i);
		c.velocity_to_pixels[i] = read_f32(buf, layout.velocity + 4 * i);
	}
	c.camera_cut = read_u32(buf, layout.camera_cut);
	const auto sane_size = [](float v) { return v >= 1.0f && v <= 16384.0f; };
	const auto sane_scale = [](float v) { return std::isfinite(v) && std::fabs(v) > 0.0f && std::fabs(v) < 65536.0f; };
	if (!sane_size(c.input_size[0]) || !sane_size(c.input_size[1]) || !sane_size(c.history_size[0]) || !sane_size(c.history_size[1]))
		return false;
	if (!(std::fabs(c.jitter[0]) <= 0.5f && std::fabs(c.jitter[1]) <= 0.5f))
		return false;
	if (!sane_scale(c.velocity_to_pixels[0]) || !sane_scale(c.velocity_to_pixels[1]))
		return false;
	out = c;
	return true;
}

// ---- two-pass mode (history at 200%) ---------------------------------------------------------------------------------------

// In two-pass mode b0's HistoryInfo describes the big history, not the output DLSS writes. The output viewport is the
// resolve's output texture, with its origin at 0.
inline TsrConstants with_output_viewport(TsrConstants c, uint32_t out_w, uint32_t out_h)
{
	c.history_min[0] = c.history_min[1] = 0;
	c.history_size[0] = static_cast<float>(out_w);
	c.history_size[1] = static_cast<float>(out_h);
	return c;
}

// The history viewport of a two-pass frame is the output size times 1 to 4 on both axes (the game uses 2). Anything else
// means the constants were misread or this is not the TSR instance whose output is replaced.
inline bool history_scale_ok(float history_w, float history_h, uint32_t out_w, uint32_t out_h)
{
	if (out_w == 0 || out_h == 0)
		return false;
	const float sx = history_w / static_cast<float>(out_w), sy = history_h / static_cast<float>(out_h);
	return sx >= 1.0f && sx <= 4.0f && sy >= 1.0f && sy <= 4.0f;
}

// The resolve belongs to the pending UpdateHistory when it reads the very resource UpdateHistory wrote (resource handles).
inline bool resolve_pairs_with_update(uint64_t update_history_out, uint64_t resolve_history_in)
{
	return update_history_out != 0 && update_history_out == resolve_history_in;
}

// When UpdateHistory may be skipped. Skipping it leaves the game's history stale, so a resolve that cannot be replaced then
// shows an old image. It is therefore only skipped after the previous two-pass frame was replaced completely by DLSS, so
// everything the resolve needs (proxies, NGX, the feature, the output binding) has just been shown to work; the first frame
// and the frame after any failure run the game's UpdateHistory and replace only the resolve.
struct TwoPassGate
{
	bool primed = false;
	bool may_skip_update() const { return primed; }
	void resolve_replaced() { primed = true; }
	void failed() { primed = false; }
};

enum class Preset { dlaa, quality, balanced, performance, ultra_performance };

// Nominal render/output width ratios of the DLSS modes; the closest one is used, there is no fixed mode.
// No UltraQuality: DLSS 3.10 refuses it at create (0xbad00010 FAIL_UnsupportedParameter).
inline Preset preset_for_ratio(double ratio)
{
	static const struct { Preset preset; double ratio; } table[] = {
		{ Preset::dlaa, 1.0 }, { Preset::quality, 2.0 / 3.0 },
		{ Preset::balanced, 0.58 }, { Preset::performance, 0.5 }, { Preset::ultra_performance, 1.0 / 3.0 },
	};
	Preset best = Preset::dlaa;
	double best_delta = 1e9;
	for (const auto &e : table)
		if (std::fabs(e.ratio - ratio) < best_delta)
		{
			best_delta = std::fabs(e.ratio - ratio);
			best = e.preset;
		}
	return best;
}

inline const char *preset_name(Preset p)
{
	switch (p)
	{
	case Preset::dlaa: return "DLAA";
	case Preset::quality: return "MaxQuality";
	case Preset::balanced: return "Balanced";
	case Preset::performance: return "MaxPerf";
	default: return "UltraPerformance";
	}
}

// DLSS render preset hint (NVSDK_NGX_DLSS_Hint_Render_Preset in NGX v310.9.16). Only the presets the SDK documents as
// usable: A-D are removed, E/F deprecated, G/H/I/N/O "revert to default". `value` is the SDK enum value; the add-on
// static_asserts them against the SDK header.
struct RenderPresetInfo { int value; const char *name; };
constexpr RenderPresetInfo kRenderPresets[] = { { 0, "Default" }, { 10, "J" }, { 11, "K" }, { 12, "L" }, { 13, "M" } };
constexpr int kRenderPresetCount = static_cast<int>(sizeof(kRenderPresets) / sizeof(kRenderPresets[0]));

// Index into kRenderPresets of an SDK value; unknown values map to Default (index 0).
inline int render_preset_index(int value)
{
	for (int i = 0; i < kRenderPresetCount; ++i)
		if (kRenderPresets[i].value == value)
			return i;
	return 0;
}

inline const char *render_preset_name(int value) { return kRenderPresets[render_preset_index(value)].name; }

// "default", "j", "k", "l", "m" in any case -> SDK value; -1 for anything else.
inline int parse_render_preset(const std::string &text)
{
	for (const RenderPresetInfo &p : kRenderPresets)
	{
		if (text.size() != std::strlen(p.name))
			continue;
		bool same = true;
		for (size_t i = 0; i < text.size(); ++i)
			same = same && std::tolower(static_cast<unsigned char>(text[i])) == std::tolower(static_cast<unsigned char>(p.name[i]));
		if (same)
			return p.value;
	}
	return -1;
}

// UE5 Engine/Shaders/Private/VelocityCommon.ush: EncodeVelocityToTexture stores V * (0.499 * 0.5) + 32767/65535.
constexpr float kVelocityBias = 32767.0f / 65535.0f;
constexpr float kVelocityScale = 0.499f * 0.5f;
inline float decode_velocity(float encoded) { return (encoded - kVelocityBias) / kVelocityScale; }
inline float encode_velocity(float v) { return v * kVelocityScale + kVelocityBias; }

// UE4-style gamma velocity: after the linear unbias the value is a signed square root of the motion scaled by
// 2/1.4142; this undoes it. Mirrors DecodeVelocityGammaUE4 in velocity_decode.hlsl.
constexpr float kGammaVelocityDivisor = 2.0f / 1.4142f;
inline float decode_velocity_gamma(float e)
{
	e /= kGammaVelocityDivisor;
	return (e < 0 ? -1.0f : 1.0f) * e * e;
}

struct Ini
{
	std::map<std::string, std::string> values;
	std::string text(const std::string &key, const std::string &fallback) const
	{
		const auto it = values.find(key);
		return it == values.end() || it->second.empty() ? fallback : it->second;
	}
	double number(const std::string &key, double fallback) const
	{
		const auto it = values.find(key);
		if (it == values.end() || it->second.empty())
			return fallback;
		char *end = nullptr;
		const double v = std::strtod(it->second.c_str(), &end);
		return end == it->second.c_str() ? fallback : v;
	}
};

inline std::string trim(const std::string &s)
{
	const size_t a = s.find_first_not_of(" \t\r\n");
	if (a == std::string::npos)
		return "";
	return s.substr(a, s.find_last_not_of(" \t\r\n") - a + 1);
}

// Plain key=value lines; blank lines and lines starting with ';' or '#' are ignored.
inline Ini parse_ini(const std::string &text)
{
	Ini ini;
	size_t pos = 0;
	while (pos <= text.size())
	{
		size_t end = text.find('\n', pos);
		if (end == std::string::npos)
			end = text.size();
		const std::string line = trim(text.substr(pos, end - pos));
		const size_t eq = line.find('=');
		if (!line.empty() && line[0] != ';' && line[0] != '#' && eq != std::string::npos)
			ini.values[trim(line.substr(0, eq))] = trim(line.substr(eq + 1));
		pos = end + 1;
	}
	return ini;
}

// Rewrites `text` so every key in `updates` has the given value, keeping comments, blank lines, line endings and the
// order of everything else. A key that appears more than once is rewritten each time; keys the file lacks are appended.
inline std::string merge_ini(const std::string &text, const std::map<std::string, std::string> &updates)
{
	std::string out;
	std::set<std::string> done;
	size_t pos = 0;
	while (pos < text.size())
	{
		size_t end = text.find('\n', pos);
		const bool has_newline = end != std::string::npos;
		if (!has_newline)
			end = text.size();
		std::string line = text.substr(pos, end - pos);
		const std::string t = trim(line);
		const size_t eq = t.find('=');
		if (!t.empty() && t[0] != ';' && t[0] != '#' && eq != std::string::npos)
		{
			const std::string key = trim(t.substr(0, eq));
			const auto it = updates.find(key);
			if (it != updates.end())
			{
				const bool cr = line.back() == '\r';
				line = key + "=" + it->second + (cr ? "\r" : "");
				done.insert(key);
			}
		}
		out += line;
		if (has_newline)
			out += '\n';
		pos = end + 1;
	}
	for (const auto &[key, value] : updates)
		if (done.count(key) == 0)
		{
			if (!out.empty() && out.back() != '\n')
				out += '\n';
			out += key + "=" + value + "\n";
		}
	return out;
}

// ---- render scale (r.ScreenPercentage in the game's Engine.ini) ------------------------------------------------------

// The DLSS mode names of the ini key render_scale and the percentage each one asks the game to render at (the nominal
// ratios of preset_for_ratio). Index 0 is "game": leave the game's own scale alone.
struct RenderScaleInfo { const char *name; const char *label; double percent; };
constexpr RenderScaleInfo kRenderScales[] = {
	{ "game", "Game default", 0 }, { "dlaa", "DLAA", 100 }, { "quality", "Quality", 66.7 },
	{ "balanced", "Balanced", 58 }, { "performance", "Performance", 50 }, { "ultra_performance", "Ultra Performance", 33.3 },
};
constexpr int kRenderScaleCount = static_cast<int>(sizeof(kRenderScales) / sizeof(kRenderScales[0]));

inline std::string lower(std::string s)
{
	for (char &c : s)
		c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
	return s;
}

// A mode name in any case, or a number from 33 to 100, -> percentage (0 = game); -1 for anything else.
inline double parse_render_scale(const std::string &text)
{
	const std::string t = lower(trim(text));
	for (const RenderScaleInfo &r : kRenderScales)
		if (t == r.name)
			return r.percent;
	char *end = nullptr;
	const double v = std::strtod(t.c_str(), &end);
	return !t.empty() && *end == '\0' && v >= 33 && v <= 100 ? v : -1;
}

// Index into kRenderScales of a percentage, -1 when it is a custom number.
inline int render_scale_index(double percent)
{
	for (int i = 0; i < kRenderScaleCount; ++i)
		if (std::fabs(percent - kRenderScales[i].percent) < 0.05)
			return i;
	return -1;
}

// One decimal at most, no trailing ".0": 66.7, 58, 50.
inline std::string percent_text(double percent)
{
	char buf[32];
	std::snprintf(buf, sizeof(buf), "%.1f", percent);
	std::string s = buf;
	if (s.size() > 2 && s.compare(s.size() - 2, 2, ".0") == 0)
		s.resize(s.size() - 2);
	return s;
}

// What the ini stores for a percentage: the mode name, or the number for a custom value.
inline std::string render_scale_text(double percent)
{
	const int i = render_scale_index(percent);
	return i >= 0 ? kRenderScales[i].name : percent_text(percent);
}

// The project name UE builds its config folder from: the folder two levels above the exe's folder when that sits under
// "Binaries" (<game>\<Project>\Binaries\Win64\<exe>), else the exe name before "-Win64". Empty when neither fits.
inline std::string project_name_from_exe(const std::string &exe_path)
{
	std::vector<std::string> parts;
	size_t pos = 0;
	while (pos <= exe_path.size())
	{
		size_t end = exe_path.find_first_of("\\/", pos);
		if (end == std::string::npos)
			end = exe_path.size();
		parts.push_back(exe_path.substr(pos, end - pos));
		pos = end + 1;
	}
	const size_t n = parts.size();
	if (n >= 4 && !parts[n - 4].empty() && lower(parts[n - 3]) == "binaries")
		return parts[n - 4];
	const size_t dash = lower(parts.back()).find("-win64");
	return dash == std::string::npos ? "" : parts.back().substr(0, dash);
}

// Engine.ini text -> Engine.ini text with r.ScreenPercentage set in [SystemSettings]. percent > 0: every
// r.ScreenPercentage line of that section is replaced by one marked line pair (kept where the first one was, else right
// under the header, else in a new section at the end). percent == 0: only a pair with the marker above is removed, so a
// player's own r.ScreenPercentage stays. A [SystemSettings] section this leaves empty loses its header. Every other
// line, each line's own ending (LF or CRLF; CRLF for lines the add-on adds to a file with none) and a BOM are kept.
constexpr const char *kScaleMarker = "; amdlss_dx12_dlss render_scale";

inline std::string set_screen_percentage(const std::string &input, double percent)
{
	struct Line { std::string body, term; };
	const bool want = percent > 0;
	std::string text = input, bom;
	if (text.compare(0, 3, "\xEF\xBB\xBF") == 0)
	{
		bom = text.substr(0, 3);
		text.erase(0, 3);
	}
	const std::string eol = text.find('\n') != std::string::npos && text.find("\r\n") == std::string::npos ? "\n" : "\r\n";

	std::vector<Line> lines;
	for (size_t pos = 0; pos < text.size();)
	{
		const size_t nl = text.find('\n', pos);
		Line l;
		l.body = text.substr(pos, nl == std::string::npos ? std::string::npos : nl - pos);
		pos = nl == std::string::npos ? text.size() : nl + 1;
		if (nl != std::string::npos)
			l.term = "\n";
		if (!l.body.empty() && l.body.back() == '\r' && nl != std::string::npos)
		{
			l.body.pop_back();
			l.term = "\r\n";
		}
		lines.push_back(l);
	}

	const auto is_header = [](const std::string &t) { return t.size() >= 2 && t.front() == '[' && t.back() == ']'; };
	const auto is_system = [&](const std::string &t) { return is_header(t) && lower(trim(t.substr(1, t.size() - 2))) == "systemsettings"; };
	const auto is_scale_key = [](const std::string &t) {
		const size_t eq = t.find('=');
		return !t.empty() && t[0] != ';' && t[0] != '#' && eq != std::string::npos && lower(trim(t.substr(0, eq))) == "r.screenpercentage";
	};

	std::vector<Line> out;
	bool in_system = false, placed = false, removed_here = false, placed_here = false;
	size_t header_at = 0;
	const auto close_section = [&](bool at_eof) {
		if (!in_system || !removed_here || placed_here)
			return;
		for (size_t i = header_at + 1; i < out.size(); ++i)
			if (!trim(out[i].body).empty())
				return;
		out.resize(header_at); // the section is empty now; at the end of the file its separating blank lines go too
		while (at_eof && !out.empty() && trim(out.back().body).empty())
			out.pop_back();
		if (at_eof && !out.empty() && out.back().term.empty())
			out.back().term = eol;
	};
	const auto scale_pair = [&](const std::string &term1, const std::string &term2) {
		return std::vector<Line> { { kScaleMarker, term1.empty() ? eol : term1 },
			{ "r.ScreenPercentage=" + percent_text(percent), term2.empty() ? eol : term2 } };
	};

	for (size_t i = 0; i < lines.size(); ++i)
	{
		const Line &l = lines[i];
		const std::string t = trim(l.body);
		if (is_header(t))
		{
			close_section(false);
			in_system = is_system(t);
			removed_here = placed_here = false;
			header_at = out.size();
			out.push_back(l);
			continue;
		}
		const bool marker = in_system && t == kScaleMarker;
		const bool marked_pair = marker && i + 1 < lines.size() && is_scale_key(trim(lines[i + 1].body));
		if (marker && !marked_pair) // a marker with nothing under it is ours and stale
		{
			removed_here = true;
			continue;
		}
		if (marked_pair || (in_system && is_scale_key(t) && want))
		{
			const Line &last = lines[i + (marked_pair ? 1 : 0)];
			removed_here = true;
			if (want && !placed)
			{
				const std::vector<Line> p = scale_pair(marked_pair ? l.term : "", last.term);
				out.insert(out.end(), p.begin(), p.end());
				placed = placed_here = true;
			}
			i += marked_pair ? 1 : 0;
			continue;
		}
		out.push_back(l);
	}
	close_section(true);

	if (want && !placed)
	{
		size_t at = 0;
		while (at < out.size() && !is_system(trim(out[at].body)))
			++at;
		if (at == out.size())
		{
			if (!out.empty())
			{
				if (out.back().term.empty())
					out.back().term = eol;
				if (!trim(out.back().body).empty())
					out.push_back({ "", eol });
			}
			out.push_back({ "[SystemSettings]", eol });
			at = out.size() - 1;
		}
		if (out[at].term.empty())
			out[at].term = eol;
		const std::vector<Line> p = scale_pair(out[at].term, out[at].term);
		out.insert(out.begin() + at + 1, p.begin(), p.end());
	}

	std::string result = bom;
	for (const Line &l : out)
		result += l.body + l.term;
	return result;
}

// ---- frame-time statistics for the perf report ---------------------------------------------------------------------

struct FrameStats
{
	size_t frames = 0;
	double avg_ms = 0, fps = 0, p99_ms = 0, low1_fps = 0;
};

// Statistics of one report window from its frame times in ms. p99 is the nearest-rank 99th percentile; the 1% low is
// 1000 / (mean of the slowest max(1, floor(n / 100)) frames). An empty series gives all zeros.
inline FrameStats frame_stats(std::vector<double> ms)
{
	FrameStats s;
	s.frames = ms.size();
	if (ms.empty())
		return s;
	std::sort(ms.begin(), ms.end());
	const size_t n = ms.size();
	double sum = 0;
	for (double v : ms)
		sum += v;
	s.avg_ms = sum / n;
	s.p99_ms = ms[static_cast<size_t>(std::ceil(0.99 * n)) - 1];
	const size_t k = n >= 100 ? n / 100 : 1; // no std::max: windows.h defines a max macro in the add-on build
	double slow = 0;
	for (size_t i = n - k; i < n; ++i)
		slow += ms[i];
	slow /= k;
	s.fps = s.avg_ms > 0 ? 1000.0 / s.avg_ms : 0;
	s.low1_fps = slow > 0 ? 1000.0 / slow : 0;
	return s;
}

// What ran on the upscale dispatches of a window: counts of successful DLSS evaluates and of dispatches the game's TSR took.
inline const char *upscaler_label(uint64_t dlss, uint64_t tsr)
{
	return dlss > 0 && tsr > 0 ? "mixed" : dlss > 0 ? "DLSS" : tsr > 0 ? "TSR" : "none";
}

// The line a script parses; `t` is seconds since the add-on loaded.
inline std::string perf_line(double t, const FrameStats &s, const char *upscaler, uint32_t rw, uint32_t rh, uint32_t ow, uint32_t oh)
{
	char buf[256];
	snprintf(buf, sizeof(buf), "perf: t=%.1f frames=%llu avg_ms=%.2f fps=%.1f low1_fps=%.1f p99_ms=%.2f upscaler=%s render=%ux%u output=%ux%u ratio=%.3f",
		t, static_cast<unsigned long long>(s.frames), s.avg_ms, s.fps, s.low1_fps, s.p99_ms, upscaler, rw, rh, ow, oh, ow != 0 ? static_cast<double>(rw) / ow : 0.0);
	return buf;
}
} // namespace logic
