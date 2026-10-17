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

	// Hashes from the 200% capture (title-history200.log, D152 and D153) and the 100% capture (D715).
	assert(tsr_pass_for_hash(0x1ff10c3ce95d3bcbull) == TsrPass::single);
	assert(tsr_pass_for_hash(0xe32f96d4dc79be5bull) == TsrPass::update_history);
	assert(tsr_pass_for_hash(0x0b1375003bfb8294ull) == TsrPass::resolve);
	assert(tsr_pass_for_hash(0x793b3ceb82f744ecull) == TsrPass::none && tsr_pass_for_hash(0) == TsrPass::none);

	// A per-hash layout moves the fields: the same bytes parse differently with shifted offsets, and a short buffer is judged
	// against the layout's own size.
	{
		assert(tsr_layout_for_hash(kHashUpdateHistory).bytes() == kTsrConstantsBytes);
		uint8_t buf[512] = {};
		const auto put_u = [&](size_t at, uint32_t v) { std::memcpy(buf + at, &v, 4); };
		const auto put_f = [&](size_t at, float v) { std::memcpy(buf + at, &v, 4); };
		const size_t shift = 16;
		put_f(48 + shift, 1552.0f); put_f(52 + shift, 876.0f);
		put_f(160 + shift, 5120.0f); put_f(164 + shift, 2880.0f);
		put_f(256 + shift, -0.25f); put_f(260 + shift, 0.125f);
		put_u(264 + shift, 0);
		put_f(272 + shift, 776.0f); put_f(276 + shift, -438.0f);
		TsrConstants c;
		assert(!parse_tsr_constants(buf, sizeof(buf), c));               // default offsets see zeros: no viewport
		TsrLayout moved = kTsrDefaultLayout;
		moved.input_min += shift; moved.input_size += shift; moved.history_min += shift; moved.history_size += shift;
		moved.jitter += shift; moved.camera_cut += shift; moved.velocity += shift;
		assert(moved.bytes() == kTsrConstantsBytes + shift);
		assert(parse_tsr_constants(buf, sizeof(buf), c, moved));
		assert(c.history_size[0] == 5120.0f && c.jitter[0] == -0.25f && c.velocity_to_pixels[1] == -438.0f);
		assert(!parse_tsr_constants(buf, moved.bytes() - 1, c, moved) && parse_tsr_constants(buf, moved.bytes(), c, moved));
	}

	// Two-pass: DLSS writes the resolve's 2560x1440 output, never the 5120x2880 history from b0.
	{
		TsrConstants c;
		c.input_size[0] = 1552; c.input_size[1] = 873;
		c.history_min[0] = 7; c.history_min[1] = 9;
		c.history_size[0] = 5120; c.history_size[1] = 2880;
		const TsrConstants o = with_output_viewport(c, 2560, 1440);
		assert(o.history_size[0] == 2560.0f && o.history_size[1] == 1440.0f && o.history_min[0] == 0 && o.history_min[1] == 0);
		assert(o.input_size[0] == 1552.0f && o.input_size[1] == 873.0f);       // the rest is untouched

		assert(history_scale_ok(5120, 2880, 2560, 1440) && history_scale_ok(2560, 1440, 2560, 1440));
		assert(!history_scale_ok(1280, 720, 2560, 1440) && !history_scale_ok(5120, 2880, 0, 0) && !history_scale_ok(20480, 2880, 2560, 1440));

		assert(resolve_pairs_with_update(0x9b4a8cd0, 0x9b4a8cd0));              // D152 u0 and D153 u2 in the capture
		assert(!resolve_pairs_with_update(0x9b4a8cd0, 0x9b3f88b0) && !resolve_pairs_with_update(0, 0));

		// The first frame and the frame after a failure keep UpdateHistory; only a fully replaced frame lets the next skip it.
		TwoPassGate g;
		assert(!g.may_skip_update());
		g.resolve_replaced();
		assert(g.may_skip_update());
		g.resolve_replaced();
		assert(g.may_skip_update());
		g.failed();
		assert(!g.may_skip_update());
		g.failed();
		assert(!g.may_skip_update());
		g.resolve_replaced();
		assert(g.may_skip_update());
	}

	const Ini ini = parse_ini("; comment\nenabled = 1\n# other\njitter_sign_y=-1\r\nmv_scale_x=-0.5 \nbroken line\n\nlog_every_n_frames=300");
	assert(ini.number("enabled", 0) == 1 && ini.number("jitter_sign_y", 1) == -1);
	assert(ini.number("mv_scale_x", 0) == -0.5 && ini.number("log_every_n_frames", 0) == 300);
	assert(parse_ini("mv_encoding = gamma\n").text("mv_encoding", "linear") == "gamma" && ini.text("missing", "linear") == "linear");
	assert(ini.number("missing", 7) == 7 && ini.values.count("broken line") == 0);

	// Render preset names: case-insensitive, unknown is -1, unknown SDK values display as Default.
	assert(parse_render_preset("default") == 0 && parse_render_preset("K") == 11 && parse_render_preset("j") == 10);
	assert(parse_render_preset("M") == 13 && parse_render_preset("l") == 12);
	assert(parse_render_preset("") == -1 && parse_render_preset("A") == -1 && parse_render_preset("Defaultx") == -1);
	assert(std::strcmp(render_preset_name(12), "L") == 0 && std::strcmp(render_preset_name(0), "Default") == 0 && std::strcmp(render_preset_name(7), "Default") == 0);
	for (const RenderPresetInfo &p : kRenderPresets)
		assert(parse_render_preset(p.name) == p.value && render_preset_index(p.value) >= 0);

	// merge_ini: comments, blank lines, CRLF and key order survive; changed keys are rewritten, missing ones appended.
	{
		const std::string src = "; header\r\nenabled = 1\r\n\r\n# note\r\njitter_sign_x=1\r\nenabled=1\r\nmv_encoding=gamma";
		const std::string out = merge_ini(src, { { "enabled", "0" }, { "render_preset", "K" }, { "mv_encoding", "linear" } });
		assert(out == "; header\r\nenabled=0\r\n\r\n# note\r\njitter_sign_x=1\r\nenabled=0\r\nmv_encoding=linear\nrender_preset=K\n");
		const Ini back = parse_ini(out);
		assert(back.number("enabled", 1) == 0 && back.text("render_preset", "") == "K" && back.text("mv_encoding", "") == "linear");
		assert(back.number("jitter_sign_x", 0) == 1);
		assert(merge_ini(out, { { "enabled", "0" } }) == out);           // writing the same value again changes nothing
		assert(merge_ini("", { { "a", "1" } }) == "a=1\n");
		assert(merge_ini("; enabled=5\n", { { "enabled", "1" } }) == "; enabled=5\nenabled=1\n"); // a commented key is not the key
	}

	// frame_stats: even series, a few spikes, one frame, none.
	{
		const auto approx = [](double a, double b) { return std::fabs(a - b) < 1e-9; };
		FrameStats even = frame_stats(std::vector<double>(100, 10.0));
		assert(even.frames == 100 && approx(even.avg_ms, 10) && approx(even.fps, 100) && approx(even.p99_ms, 10) && approx(even.low1_fps, 100));

		std::vector<double> spiky(98, 10.0);
		spiky.push_back(50.0);
		spiky.push_back(50.0);
		const FrameStats sp = frame_stats(spiky);   // 100 frames: the slowest 1% is one frame
		assert(sp.frames == 100 && approx(sp.avg_ms, 10.8) && std::fabs(sp.fps - 1000 / 10.8) < 1e-9);
		assert(approx(sp.p99_ms, 50) && approx(sp.low1_fps, 20));
		std::vector<double> big(300, 10.0);
		big[7] = big[100] = big[299] = 40.0;         // 300 frames: the slowest 1% is three frames, here all spikes
		assert(approx(frame_stats(big).low1_fps, 25) && approx(frame_stats(big).p99_ms, 10));
		std::vector<double> few = { 30.0, 10.0, 20.0 }; // unsorted input, under 100 frames: at least one frame
		const FrameStats fs = frame_stats(few);
		assert(approx(fs.avg_ms, 20) && approx(fs.p99_ms, 30) && approx(fs.low1_fps, 1000.0 / 30));

		const FrameStats one = frame_stats({ 20.0 });
		assert(one.frames == 1 && approx(one.avg_ms, 20) && approx(one.fps, 50) && approx(one.p99_ms, 20) && approx(one.low1_fps, 50));
		const FrameStats none = frame_stats({});
		assert(none.frames == 0 && none.avg_ms == 0 && none.fps == 0 && none.p99_ms == 0 && none.low1_fps == 0);

		assert(std::strcmp(upscaler_label(5, 0), "DLSS") == 0 && std::strcmp(upscaler_label(0, 3), "TSR") == 0);
		assert(std::strcmp(upscaler_label(5, 1), "mixed") == 0 && std::strcmp(upscaler_label(0, 0), "none") == 0);

		assert(perf_line(12.34, sp, "DLSS", 1552, 873, 2560, 1440) ==
			"perf: t=12.3 frames=100 avg_ms=10.80 fps=92.6 low1_fps=20.0 p99_ms=50.00 upscaler=DLSS render=1552x873 output=2560x1440 ratio=0.606");
		assert(perf_line(5.0, none, "none", 0, 0, 0, 0) ==
			"perf: t=5.0 frames=0 avg_ms=0.00 fps=0.0 low1_fps=0.0 p99_ms=0.00 upscaler=none render=0x0 output=0x0 ratio=0.000");
	}

	std::puts("dlss logic selftest ok");
	return 0;
}
