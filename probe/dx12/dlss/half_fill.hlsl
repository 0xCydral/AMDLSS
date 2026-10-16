// Fills TSR's half-size output (u1, r11g11b10_float, half the output size) from the DLSS output (u0).
//
// UE5 feeds that texture to the post-process downsample chain (bloom, eye adaptation). With the TSR dispatch skipped
// nothing writes it, and RDG may have aliased its memory with another pass, so those passes read garbage.
// Each destination texel is the average of the 2x2 block of u0 at twice its coordinates; reads are clamped so odd
// sizes work. Sizes arrive as root constants, so nothing is hard-coded.
//
// dxc -T cs_6_0 -E main half_fill.hlsl

#define RS "RootConstants(num32BitConstants = 4, b0), DescriptorTable(SRV(t0), UAV(u0))"

cbuffer Constants : register(b0)
{
    uint2 DstSize;
    uint2 SrcSize;
};

Texture2D<float4> Src : register(t0);
RWTexture2D<float4> Dst : register(u0);

[RootSignature(RS)]
[numthreads(8, 8, 1)]
void main(uint3 id : SV_DispatchThreadID)
{
    if (any(id.xy >= DstSize))
        return;
    int2 top_left = int2(id.xy) * 2;
    int2 max_texel = int2(SrcSize) - 1;
    float4 sum = Src.Load(int3(min(top_left, max_texel), 0))
               + Src.Load(int3(min(top_left + int2(1, 0), max_texel), 0))
               + Src.Load(int3(min(top_left + int2(0, 1), max_texel), 0))
               + Src.Load(int3(min(top_left + int2(1, 1), max_texel), 0));
    Dst[id.xy] = sum * 0.25;
}
