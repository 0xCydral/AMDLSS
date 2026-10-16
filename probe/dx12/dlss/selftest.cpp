// Self-check of logic.hpp. build.sh compiles and runs it on the Linux host inside the build container.
#include <cassert>
#include <cstring>
#include <cstdio>

#include "logic.hpp"

int main()
{
	using namespace logic;

	assert(halton(1, 2) == 0.5 && halton(2, 2) == 0.25 && halton(3, 2) == 0.75);
	assert(std::fabs(halton(4, 3) - (1.0 / 3 + 1.0 / 9)) < 1e-12);

	// The pair measured in the T7 capture: frame 0 = (+0.375, +0.055556) is Halton index 7, phase 6.
	assert(match_phase(0.375f, 0.055556f, 8) == 6);
	assert(match_phase(0.3f, 0.3f, 8) == -1);
	float x, y;
	predict_jitter(6, 2, 8, x, y);                       // two frames later the sequence wraps to index 1
	assert(std::fabs(x - 0.0f) < 1e-6 && std::fabs(y + 1.0f / 6) < 1e-6);
	predict_jitter(6, 9, 8, x, y);                       // a whole cycle later is the same pair
	assert(match_phase(x, y, 8) == 7);

	assert(preset_for_ratio(1552.0 / 2560.0) == Preset::balanced);
	assert(preset_for_ratio(1.0) == Preset::dlaa && preset_for_ratio(0.5) == Preset::performance);
	assert(preset_for_ratio(1.0 / 3) == Preset::ultra_performance && preset_for_ratio(0.66) == Preset::quality);
	assert(preset_for_ratio(1400.0 / 1920.0) == Preset::quality); // 0.729: DLSS 3.10 has no UltraQuality

	// 32767/65535 is "no motion"; +-1 NDC maps inside [0, 1] with the 0.499 safety margin.
	assert(std::fabs(decode_velocity(kVelocityBias)) < 1e-6);
	for (float v : { -1.0f, -0.25f, 0.0f, 0.5f, 1.0f })
		assert(std::fabs(decode_velocity(encode_velocity(v)) - v) < 1e-5);
	assert(encode_velocity(1.0f) < 1.0f && encode_velocity(-1.0f) > 0.0f);

	// Gamma velocity: signed square, zero stays zero, the divisor is 2/1.4142.
	assert(decode_velocity_gamma(0.0f) == 0.0f);
	assert(std::fabs(decode_velocity_gamma(kGammaVelocityDivisor) - 1.0f) < 1e-6);
	assert(std::fabs(decode_velocity_gamma(-kGammaVelocityDivisor / 2) + 0.25f) < 1e-6);

	// TSR's b0 as seen live: a 512-byte buffer with the fields at the offsets from the shader disassembly.
	{
		uint8_t buf[512] = {};
		const auto put_u = [&](size_t at, uint32_t v) { std::memcpy(buf + at, &v, 4); };
		const auto put_f = [&](size_t at, float v) { std::memcpy(buf + at, &v, 4); };
		put_u(32, 2); put_u(36, 3);                    // InputInfo_ViewportMin
		put_u(40, 1554); put_u(44, 876);               // InputInfo_ViewportMax
		put_f(48, 1552.0f); put_f(52, 873.0f);         // InputInfo_ViewportSize
		put_u(144, 4); put_u(148, 5);                  // HistoryInfo_ViewportMin
		put_f(160, 2560.0f); put_f(164, 1440.0f);      // HistoryInfo_ViewportSize
		put_f(256, 0.375f); put_f(260, 0.055556f);     // InputJitter
		put_u(264, 1);                                 // bCameraCut
		put_f(272, 776.0f); put_f(276, -436.5f);       // ScreenVelocityToInputPixelVelocity
		TsrConstants c;
		assert(parse_tsr_constants(buf, sizeof(buf), c));
		assert(c.input_min[0] == 2 && c.input_min[1] == 3 && c.history_min[0] == 4 && c.history_min[1] == 5);
		assert(c.input_size[0] == 1552.0f && c.input_size[1] == 873.0f && c.history_size[0] == 2560.0f && c.history_size[1] == 1440.0f);
		assert(c.jitter[0] == 0.375f && std::fabs(c.jitter[1] - 0.055556f) < 1e-7);
		assert(c.camera_cut == 1 && c.velocity_to_pixels[0] == 776.0f && c.velocity_to_pixels[1] == -436.5f);

		assert(!parse_tsr_constants(buf, kTsrConstantsBytes - 1, c));   // too short
		put_f(256, 0.9f);                                               // jitter outside +-0.5
		assert(!parse_tsr_constants(buf, sizeof(buf), c));
		put_f(256, 0.375f);
		put_f(48, 0.0f);                                                // an empty viewport is not a TSR frame
		assert(!parse_tsr_constants(buf, sizeof(buf), c));
		put_f(48, 1552.0f);
		put_f(272, 0.0f);                                               // velocity scale of zero
		assert(!parse_tsr_constants(buf, sizeof(buf), c));
	}

	const Ini ini = parse_ini("; comment\nenabled = 1\n# other\njitter_sign_y=-1\r\nmv_scale_x=-0.5 \nbroken line\n\nlog_every_n_frames=300");
	assert(ini.number("enabled", 0) == 1 && ini.number("jitter_sign_y", 1) == -1);
	assert(ini.number("mv_scale_x", 0) == -0.5 && ini.number("log_every_n_frames", 0) == 300);
	assert(parse_ini("mv_encoding = gamma\n").text("mv_encoding", "linear") == "gamma" && ini.text("missing", "linear") == "linear");
	assert(ini.number("missing", 7) == 7 && ini.values.count("broken line") == 0);

	std::puts("dlss logic selftest ok");
	return 0;
}
