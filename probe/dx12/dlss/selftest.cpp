// Self-check of logic.hpp. build.sh compiles and runs it on the Linux host inside the build container.
#include <cassert>
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
	assert(preset_for_ratio(1400.0 / 1920.0) == Preset::ultra_quality); // 0.729 is closer to 0.77 than to 0.667

	// 32767/65535 is "no motion"; +-1 NDC maps inside [0, 1] with the 0.499 safety margin.
	assert(std::fabs(decode_velocity(kVelocityBias)) < 1e-6);
	for (float v : { -1.0f, -0.25f, 0.0f, 0.5f, 1.0f })
		assert(std::fabs(decode_velocity(encode_velocity(v)) - v) < 1e-5);
	assert(encode_velocity(1.0f) < 1.0f && encode_velocity(-1.0f) > 0.0f);

	// Gamma velocity: signed square, zero stays zero, the divisor is 2/1.4142.
	assert(decode_velocity_gamma(0.0f) == 0.0f);
	assert(std::fabs(decode_velocity_gamma(kGammaVelocityDivisor) - 1.0f) < 1e-6);
	assert(std::fabs(decode_velocity_gamma(-kGammaVelocityDivisor / 2) + 0.25f) < 1e-6);

	const Ini ini = parse_ini("; comment\nenabled = 1\n# other\njitter_sign_y=-1\r\nmv_scale_x=-0.5 \nbroken line\n\nlog_every_n_frames=300");
	assert(ini.number("enabled", 0) == 1 && ini.number("jitter_sign_y", 1) == -1);
	assert(ini.number("mv_scale_x", 0) == -0.5 && ini.number("log_every_n_frames", 0) == 300);
	assert(parse_ini("mv_encoding = gamma\n").text("mv_encoding", "linear") == "gamma" && ini.text("missing", "linear") == "linear");
	assert(ini.number("missing", 7) == 7 && ini.values.count("broken line") == 0);

	std::puts("dlss logic selftest ok");
	return 0;
}
