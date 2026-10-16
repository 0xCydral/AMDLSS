/*
 * Pure logic of the DLSS add-on (no Windows, no ReShade, no NGX), so selftest.cpp can check it on the Linux host
 * inside the build container: Halton jitter, jitter phase matching, preset choice, UE5 velocity decode, ini parsing.
 */
#pragma once

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <map>
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

enum class Preset { dlaa, ultra_quality, quality, balanced, performance, ultra_performance };

// Nominal render/output width ratios of the DLSS modes; the closest one is used, there is no fixed mode.
inline Preset preset_for_ratio(double ratio)
{
	static const struct { Preset preset; double ratio; } table[] = {
		{ Preset::dlaa, 1.0 }, { Preset::ultra_quality, 0.77 }, { Preset::quality, 2.0 / 3.0 },
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
	case Preset::ultra_quality: return "UltraQuality";
	case Preset::quality: return "MaxQuality";
	case Preset::balanced: return "Balanced";
	case Preset::performance: return "MaxPerf";
	default: return "UltraPerformance";
	}
}

// UE5 Engine/Shaders/Private/VelocityCommon.ush: EncodeVelocityToTexture stores V * (0.499 * 0.5) + 32767/65535.
constexpr float kVelocityBias = 32767.0f / 65535.0f;
constexpr float kVelocityScale = 0.499f * 0.5f;
inline float decode_velocity(float encoded) { return (encoded - kVelocityBias) / kVelocityScale; }
inline float encode_velocity(float v) { return v * kVelocityScale + kVelocityBias; }

struct Ini
{
	std::map<std::string, std::string> values;
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
} // namespace logic
