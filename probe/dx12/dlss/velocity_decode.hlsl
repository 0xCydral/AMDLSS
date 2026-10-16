// Turns UE5's velocity texture (r16g16_unorm, biased) into a DLSS motion-vector texture (r16g16_float, render pixels).
//
// UE5 Engine/Shaders/Private/VelocityCommon.ush, EncodeVelocityToTexture:
//     Encoded.xy = V.xy * (0.499 * 0.5) + 32767 / 65535       V = NDC delta, current minus previous position
// DLSS wants previous minus current in render pixels, y down, so the scale is (-0.5 * width, +0.5 * height) times
// the ini factors. The scale reaches the shader as root constants so the add-on can flip signs without a rebuild.
//
// dxc -T cs_6_0 -E main velocity_decode.hlsl

#define RS "RootConstants(num32BitConstants = 4, b0), DescriptorTable(SRV(t0), UAV(u0))"

cbuffer Constants : register(b0)
{
    float2 Scale;   // NDC delta -> pixels, sign included
    uint2 Size;     // render size
};

Texture2D<float2> Velocity : register(t0);
RWTexture2D<float2> MotionVectors : register(u0);

static const float kBias = 32767.0 / 65535.0;
static const float kInvScale = 1.0 / (0.499 * 0.5);

[RootSignature(RS)]
[numthreads(8, 8, 1)]
void main(uint3 id : SV_DispatchThreadID)
{
    if (any(id.xy >= Size))
        return;
    float2 encoded = Velocity.Load(int3(id.xy, 0));
    MotionVectors[id.xy] = (encoded - kBias) * kInvScale * Scale;
}
