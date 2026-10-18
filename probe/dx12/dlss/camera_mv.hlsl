// Motion vectors for DLSS from UE4.27's TemporalAA inputs (Project Wingman), one dispatch over the input viewport.
//
// UE4 writes velocity only for pixels that move on their own (skinned or moving meshes); every other pixel has raw
// x == 0 and the TAA shader (5165782d00fcf560, see odd/tasks/inject-dlss-ue4-taa.md) reprojects it with the camera:
//   enc.x > 0 : V = enc.xy * 4.008016 - 2.0039775   (linear unbias, ScreenPos units, current minus previous, y up)
//   enc.x == 0: sp = (2u - 1, 1 - 2v) of the pixel, prevclip = sp.x*R0 + sp.y*R1 + depth*R2 + R3 (the .xyw of each row of
//               View.ClipToPrevClip, cb1[122..125]), V = sp - prevclip.xy / prevclip.w
// DLSS wants previous minus current in input pixels, y down: mv = (-V.x, V.y) * 0.5 * ViewSize.
// The shader takes sp from the unjittered position of the sample; Unjitter = 1 subtracts the jitter first (ini
// ue4_mv_unjitter), 0 uses the pixel centre as it is, so both readings can be tried on the game.
//
// dxc -T cs_6_0 -E main camera_mv.hlsl

#define RS "RootConstants(num32BitConstants = 24, b0), DescriptorTable(SRV(t0, numDescriptors = 2), UAV(u0, numDescriptors = 1))"

cbuffer Constants : register(b0)
{
    float4 Row0;     // ClipToPrevClip rows, as UE stores them (row vectors, v * M)
    float4 Row1;
    float4 Row2;
    float4 Row3;
    uint2 ViewMin;   // input viewport: only texels inside it are written
    uint2 ViewSize;
    float2 Jitter;   // TemporalJitterPixels, input pixels
    uint Unjitter;
};

Texture2D<float> Depth : register(t0);          // depth plane of the d32s8 buffer (r32_float_x8 view), reversed Z
Texture2D<float4> Velocity : register(t1);      // r16g16b16a16_unorm, only .xy is used
RWTexture2D<float2> MotionVectors : register(u0);

[RootSignature(RS)]
[numthreads(8, 8, 1)]
void main(uint3 id : SV_DispatchThreadID)
{
    if (any(id.xy >= ViewSize))
        return;
    uint2 texel = id.xy + ViewMin;
    float2 enc = Velocity.Load(int3(texel, 0)).xy;
    float2 v;
    if (enc.x > 0.0)
    {
        v = enc * 4.008016 - 2.0039775;
    }
    else
    {
        float2 p = float2(id.xy) + 0.5;
        if (Unjitter != 0)
            p -= Jitter;
        float2 uv = p / float2(ViewSize);
        float2 sp = float2(uv.x * 2.0 - 1.0, 1.0 - uv.y * 2.0);
        float depth = Depth.Load(int3(texel, 0));
        float3 pc = sp.x * Row0.xyw + sp.y * Row1.xyw + depth * Row2.xyw + Row3.xyw;
        v = abs(pc.z) > 1e-6 ? sp - pc.xy / pc.z : float2(0.0, 0.0);
    }
    MotionVectors[texel] = float2(-v.x, v.y) * 0.5 * float2(ViewSize);
}
