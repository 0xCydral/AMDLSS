// One dispatch, two jobs for DLSS:
//   1. Turns UE5's velocity texture (r16g16_unorm, biased) into a DLSS motion-vector texture (r16g16_float, pixels).
//   2. Composites separate translucency over the scene color into an r16g16b16a16_float texture DLSS uses as its color.
//
// UE5 Engine/Shaders/Private/VelocityCommon.ush, EncodeVelocityToTexture:
//     Encoded.xy = V.xy * (0.499 * 0.5) + 32767 / 65535       V = NDC delta, current minus previous position
// DLSS wants previous minus current in render pixels, y down, so Scale is (-0.5 * width, +0.5 * height) times the ini
// factors. UE4 stored velocity gamma encoded instead; Encoding = 1 selects that decode (ini mv_encoding=gamma).
// Translucency is premultiplied with alpha = transmittance: color.rgb = scene.rgb * trans.a + trans.rgb.
// Everything arrives as root constants, so the ini can change at run time without a shader rebuild.
//
// dxc -T cs_6_0 -E main velocity_decode.hlsl

#define RS "RootConstants(num32BitConstants = 6, b0), DescriptorTable(SRV(t0, numDescriptors = 3), UAV(u0, numDescriptors = 2))"

cbuffer Constants : register(b0)
{
    float2 Scale;   // NDC delta -> pixels, sign included
    uint2 Size;     // render size
    uint Encoding;  // 0 = linear (UE5), 1 = gamma (UE4 style)
    uint Composite; // 1 = write the composited color
};

Texture2D<float2> Velocity : register(t0);
Texture2D<float4> Color : register(t1);
Texture2D<float4> Translucency : register(t2);
RWTexture2D<float2> MotionVectors : register(u0);
RWTexture2D<float4> Composited : register(u1);

static const float kBias = 32767.0 / 65535.0;
static const float kInvScale = 1.0 / (0.499 * 0.5);

float2 DecodeVelocityUnbias(float2 encoded)
{
    return (encoded - kBias) * kInvScale;
}

// UE4-style gamma velocity: the unbiased value is a signed square root of the motion, scaled by 2 / 1.4142.
float2 DecodeVelocityGammaUE4(float2 e)
{
    e /= (2.0 / 1.4142);
    return sign(e) * e * e;
}

[RootSignature(RS)]
[numthreads(8, 8, 1)]
void main(uint3 id : SV_DispatchThreadID)
{
    if (any(id.xy >= Size))
        return;
    float2 e = DecodeVelocityUnbias(Velocity.Load(int3(id.xy, 0)));
    float2 v = Encoding == 1 ? DecodeVelocityGammaUE4(e) : e;
    MotionVectors[id.xy] = v * Scale;

    if (Composite != 0)
    {
        float4 scene = Color.Load(int3(id.xy, 0));
        float4 trans = Translucency.Load(int3(id.xy, 0));
        Composited[id.xy] = float4(scene.rgb * trans.a + trans.rgb, scene.a);
    }
}
