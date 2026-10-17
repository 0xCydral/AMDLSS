// One dispatch, two jobs for DLSS:
//   1. Turns UE5's velocity texture (r16g16_unorm, biased) into a DLSS motion-vector texture (r16g16_float, pixels).
//   2. Composites separate translucency over the scene color into an r16g16b16a16_float texture DLSS uses as its color.
//
// UE5 Engine/Shaders/Private/VelocityCommon.ush, EncodeVelocityToTexture:
//     Encoded.xy = V.xy * (0.499 * 0.5) + 32767 / 65535       V = NDC delta, current minus previous position
// UE's TSR shader (disassembled) decodes it as e = 4.008016 * enc - 2.003992; V = e * abs(e) * 0.5, which is the gamma
// form below (Encoding = 1, ini mv_encoding=gamma, the default), and then PrevScreenPos = ScreenPos - V, so V is current
// minus previous. DLSS wants previous minus current in input pixels, so Scale is minus the game's
// ScreenVelocityToInputPixelVelocity (b0 @272), or the ini factors times the viewport size.
// Translucency is premultiplied with alpha = transmittance: color.rgb = scene.rgb * trans.a + trans.rgb.
// Everything arrives as root constants, so the ini can change at run time without a shader rebuild.
//
// dxc -T cs_6_0 -E main velocity_decode.hlsl

#define RS "RootConstants(num32BitConstants = 8, b0), DescriptorTable(SRV(t0, numDescriptors = 3), UAV(u0, numDescriptors = 2))"

cbuffer Constants : register(b0)
{
    float2 Scale;    // velocity -> input pixels in DLSS's direction (current to previous), sign included
    uint2 ViewMin;   // input viewport: only texels inside it are written, the rest of the texture is left alone
    uint2 ViewSize;
    uint Encoding;   // 0 = linear (UE5 unbias only), 1 = gamma (what UE's TSR shader does)
    uint Composite;  // 1 = write the composited color
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
    if (any(id.xy >= ViewSize))
        return;
    uint2 texel = id.xy + ViewMin;
    float2 e = DecodeVelocityUnbias(Velocity.Load(int3(texel, 0)));
    float2 v = Encoding == 1 ? DecodeVelocityGammaUE4(e) : e;
    MotionVectors[texel] = v * Scale;

    if (Composite != 0)
    {
        float4 scene = Color.Load(int3(texel, 0));
        float4 trans = Translucency.Load(int3(texel, 0));
        Composited[texel] = float4(scene.rgb * trans.a + trans.rgb, scene.a);
    }
}
