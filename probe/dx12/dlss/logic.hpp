/*
 * Pure logic of the DLSS add-on (no Windows, no ReShade, no NGX), so selftest.cpp can check it on the Linux host
 * inside the build container: Halton jitter, jitter phase matching, preset choice, UE5 velocity decode, ini parsing
 * and merging, DLSS render preset names.
 */
#pragma once

#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <string>

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

// The fields of TSR's root constant buffer b0 (_RootShaderParameters) that the DLSS add-on needs. The byte offsets
// come from the disassembly of UE's own shader (T8, tsr_1ff10c3ce95d3bcb.dxil).
constexpr size_t kTsrInputViewportMin = 32;      // uint2, then InputInfo_ViewportMax uint2 @40
constexpr size_t kTsrInputViewportSize = 48;     // float2
constexpr size_t kTsrHistoryViewportMin = 144;   // uint2
constexpr size_t kTsrHistoryViewportSize = 160;  // float2
constexpr size_t kTsrJitter = 256;               // float2, render pixels
constexpr size_t kTsrCameraCut = 264;            // uint
constexpr size_t kTsrVelocityToPixels = 272;     // float2, ScreenVelocityToInputPixelVelocity
constexpr size_t kTsrConstantsBytes = 280;       // bytes of b0 the add-on reads

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
inline bool parse_tsr_constants(const uint8_t *buf, size_t size, TsrConstants &out)
{
	if (buf == nullptr || size < kTsrConstantsBytes)
		return false;
	TsrConstants c;
	for (int i = 0; i < 2; ++i)
	{
		c.input_min[i] = read_u32(buf, kTsrInputViewportMin + 4 * i);
		c.history_min[i] = read_u32(buf, kTsrHistoryViewportMin + 4 * i);
		c.input_size[i] = read_f32(buf, kTsrInputViewportSize + 4 * i);
		c.history_size[i] = read_f32(buf, kTsrHistoryViewportSize + 4 * i);
		c.jitter[i] = read_f32(buf, kTsrJitter + 4 * i);
		c.velocity_to_pixels[i] = read_f32(buf, kTsrVelocityToPixels + 4 * i);
	}
	c.camera_cut = read_u32(buf, kTsrCameraCut);
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
} // namespace logic
