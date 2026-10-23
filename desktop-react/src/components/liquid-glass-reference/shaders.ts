// Verbatim LiquidGlass_1.js shader strings. Do not edit the GLSL.
// See provenance.json for source URLs and SHA-256 hashes.
export const liquidVertexShader = `
attribute vec4 a_position;
attribute vec2 a_texCoord;
varying vec2 v_texCoord;
void main() {
  gl_Position = a_position;
  v_texCoord = a_texCoord;
}
`;

export const liquidFragmentShader = `
#extension GL_OES_standard_derivatives : enable
precision highp float;

uniform sampler2D u_texture;
uniform vec2 u_resolution;
uniform vec4 u_globalBounds;
uniform vec4 u_roiBounds;
uniform vec2 u_positions[50];
uniform vec2 u_lensSizes[50];
uniform vec4 u_tintColors[50];
uniform vec4 u_lensSettings[50];
uniform vec4 u_lensOptical[50];
uniform vec4 u_lensExtra[50];
uniform int u_lensCount;
uniform float u_zoomAmount;
uniform float u_time;

uniform float u_blurSamples;
uniform float u_rimSamples;

uniform bool u_enableZIndex;
uniform bool u_mergeMode;
uniform float u_mergeSmoothness;

uniform bool u_enableBackdropBlur;
uniform bool u_enableReflections;
uniform float u_reflectionReach;
uniform float u_reflectionBlur;
uniform float u_reflectionSqueeze;
uniform float u_reflectionRayDir;
uniform float u_reflectionFade;
uniform float u_reflectionRimSoftness;
varying vec2 v_texCoord;

vec2 mapScreenToRoiUv(vec2 screenUv) {
    vec2 screenCanvasUv = vec2(screenUv.x, 1.0 - screenUv.y);
    vec2 roiUv = (screenCanvasUv - u_roiBounds.xy) / u_roiBounds.zw;
    return clamp(roiUv, 0.0, 1.0);
}

vec2 fastRotate(vec2 v, float a) { 
    float s = sin(a); 
    float c = cos(a); 
    return vec2(c * v.x - s * v.y, s * v.x + c * v.y); 
}

vec2 warpDomain(vec2 p, float time, float amp, vec2 size) {
    if (amp <= 0.0001) return p;
    float minDim = min(size.x, size.y);
    float scale = 4.5 / minDim;
    float dist = length(p);
    float angle = atan(p.y, p.x);
    
    float w1 = sin(angle * 3.0 + time * 2.2);
    float w2 = cos(angle * 5.0 - time * 1.7 + dist * scale) * 0.55;
    float w3 = sin(angle * 2.0 + time * 2.9 - dist * scale * 0.8) * 0.35;
    float w4 = cos(angle * 7.0 + time * 3.8) * 0.18;
    float radialWobble = (w1 + w2 + w3 + w4) * 0.48 * amp * minDim * 0.075;
    
    vec2 radialDir = dist > 0.0001 ? (p / dist) : vec2(0.0, 1.0);
    
    vec2 vort = vec2(
        sin(p.y * scale * 1.3 + time * 2.1),
        cos(p.x * scale * 1.3 - time * 1.9)
    ) * (amp * minDim * 0.032) * smoothstep(minDim * 0.7, 0.0, dist);
    
    return p - (radialDir * radialWobble) + vort;
}

float getIGN(vec2 p) {
    return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715))));
}

vec3 frostedGlassBlur(sampler2D tex, vec2 screenUv, vec2 resolution, float radius, vec2 abOffset) {
    vec2 centerRoiUv = mapScreenToRoiUv(screenUv);
    vec2 abOffsetRoi = abOffset / u_roiBounds.zw;

    if (radius <= 0.001) {
        return texture2D(tex, centerRoiUv).rgb;
    }

    vec3 color = vec3(0.0);
    float weightSum = 0.0;
    bool hasAb = dot(abOffset, abOffset) > 0.000001;

    int count = int(min(u_blurSamples, 96.0));
    float invCount = 1.0 / float(count);
    const float gaussSharp = 1.85;
    const vec2 rotStep = vec2(-0.7373937, 0.67546318);

    float noise = getIGN(gl_FragCoord.xy);
    float startAngle = noise * 6.2831853;
    vec2 dir = vec2(cos(startAngle), sin(startAngle));
    vec2 invRes = (radius * 0.95) / (resolution * u_roiBounds.zw);

    for (int i = 0; i < 96; i++) {
        if (i >= count) break;
        float r = sqrt((float(i) + noise) * invCount);
        dir = vec2(dir.x * rotStep.x - dir.y * rotStep.y, dir.x * rotStep.y + dir.y * rotStep.x);
        vec2 sampleUV = clamp(centerRoiUv + dir * (r * invRes), 0.0, 1.0);
        float w = exp(-(float(i) + 1.0) * invCount * gaussSharp);

        if (hasAb) {
            color.r += w * texture2D(tex, clamp(sampleUV + abOffsetRoi, 0.0, 1.0)).r;
            color.g += w * texture2D(tex, sampleUV).g;
            color.b += w * texture2D(tex, clamp(sampleUV - abOffsetRoi, 0.0, 1.0)).b;
        } else {
            color += w * texture2D(tex, sampleUV).rgb;
        }
        weightSum += w;
    }
    return color / weightSum;
}

vec3 denseSpiralBlur(sampler2D tex, vec2 screenUv, vec2 resolution, float radius) {
    vec2 centerRoiUv = mapScreenToRoiUv(screenUv);
    if (radius <= 0.001) return texture2D(tex, centerRoiUv).rgb;
    vec3 color = vec3(0.0);
    int count = int(min(u_rimSamples, 16.0));
    float invCount = 1.0 / float(count);

    float noise = getIGN(gl_FragCoord.xy);
    float startAngle = noise * 6.2831853;
    vec2 dir = vec2(cos(startAngle), sin(startAngle));
    const vec2 rotStep = vec2(-0.7373937, 0.67546318);
    vec2 invRes = (radius * 0.9) / (resolution * u_roiBounds.zw);

    for (int i = 0; i < 16; i++) {
        if (i >= count) break;
        float r = sqrt((float(i) + noise) * invCount);
        dir = vec2(dir.x * rotStep.x - dir.y * rotStep.y, dir.x * rotStep.y + dir.y * rotStep.x);
        color += texture2D(tex, clamp(centerRoiUv + dir * (r * invRes), 0.0, 1.0)).rgb;
    }
    return color * invCount;
}

float smin(float a, float b, float k) {
    float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

float getLensSDF(vec2 p, vec2 center, vec2 size, vec4 settings, vec4 extra) {
    float rot = settings.x;
    float shape = settings.w;
    float wobbleAmp = extra.x;
    
    vec2 pixel_diff = (p - center) * u_resolution;
    float maxDim = max(size.x, size.y);
    float maxR = maxDim * 0.75 + (wobbleAmp > 0.0 ? wobbleAmp * min(size.x, size.y) * 0.12 : 0.0);
    float distSq = dot(pixel_diff, pixel_diff);
    if (distSq > (maxR + 120.0) * (maxR + 120.0)) {
        return sqrt(distSq) - maxDim * 0.5;
    }

    vec2 pos = fastRotate(pixel_diff, -rot);
    vec2 p_warped = warpDomain(pos, u_time, wobbleAmp, size);

    if (shape >= 0.0) {
        float r = min(shape, min(size.x, size.y) * 0.5);
        vec2 extents = (size * 0.5) - vec2(r);
        vec2 d = abs(p_warped) - extents;
        return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0) - r;
    } else {
        float r = (size.x + size.y) * 0.25;
        return length(p_warped) - r;
    }
}

float mapGlobal(vec2 p) {
    float d = 1e10;
    float k = max(u_mergeSmoothness * min(u_resolution.x, u_resolution.y), 0.1);
    for (int i = 0; i < 50; i++) {
        if (i >= u_lensCount) break;
        float dLens = getLensSDF(p, u_positions[i], u_lensSizes[i], u_lensSettings[i], u_lensExtra[i]);
        d = (i == 0) ? dLens : smin(d, dLens, k);
    }
    return d;
}

struct LensResult { bool isInEffect; float distNorm; vec2 pixelDir; vec2 pixelDiffShape; };

LensResult calculateLens(vec2 uv, vec2 centerPos, vec2 size, vec4 settings, vec4 extra) {
    float rot = settings.x;
    float shape = settings.w;
    float wobbleAmp = extra.x;
    
    LensResult res; 
    res.isInEffect = false; 
    res.distNorm = 0.0; 
    res.pixelDir = vec2(0.0); 
    res.pixelDiffShape = vec2(0.0);
    
    vec2 pixel_diff_orig = (uv - centerPos) * u_resolution;
    float maxRadius = max(size.x, size.y) * 0.75 + (wobbleAmp > 0.0 ? wobbleAmp * min(size.x, size.y) * 0.12 : 0.0);
    if (dot(pixel_diff_orig, pixel_diff_orig) > maxRadius * maxRadius) {
        return res;
    }

    vec2 pixel_diff_shape = fastRotate(pixel_diff_orig, -rot);
    vec2 p_warped = warpDomain(pixel_diff_shape, u_time, wobbleAmp, size);
    res.pixelDiffShape = p_warped;

    if (shape >= 0.0) {
        float r = min(shape, min(size.x, size.y) * 0.5);
        vec2 extents = (size * 0.5) - vec2(r);
        vec2 q = abs(p_warped) - extents;
        float d = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
        
        if (d < 0.0) {
            res.isInEffect = true;
            float maxBevel = max(min(size.x, size.y) * 0.5, 1.0);
            res.distNorm = clamp(1.0 - (-d / maxBevel), 0.0, 1.0);
            
            vec2 signP = vec2(p_warped.x >= 0.0 ? 1.0 : -1.0, p_warped.y >= 0.0 ? 1.0 : -1.0);
            vec2 localNormal;
            if (q.x > 0.0 && q.y > 0.0) {
                localNormal = normalize(q) * signP;
            } else if (q.x > q.y) {
                localNormal = vec2(signP.x, 0.0);
            } else {
                localNormal = vec2(0.0, signP.y);
            }
            
            res.pixelDir = fastRotate(localNormal, rot);
        }
    } else {
        vec2 semiAxes = size * 0.5;
        if (semiAxes.x > 0.0 && semiAxes.y > 0.0) {
            vec2 norm_diff = p_warped / semiAxes;
            float dist_sq = dot(norm_diff, norm_diff);
            if (dist_sq < 1.0) { 
                res.isInEffect = true; 
                res.distNorm = clamp(sqrt(dist_sq), 0.0, 1.0); 
                vec2 localNormal = length(norm_diff) > 0.0001 ? normalize(norm_diff / semiAxes) : vec2(0.0, 1.0);
                res.pixelDir = fastRotate(localNormal, rot);
            }
        }
    }
    return res;
}

void main() {
    vec2 st = v_texCoord;

    if (st.x < u_globalBounds.x || st.x > u_globalBounds.z || st.y < u_globalBounds.y || st.y > u_globalBounds.w) {
        discard;
    }

    bool anyInEffect = false;
    float topEdgeAlpha = 0.0;
    vec2 effectUV = st;
    vec3 gelColor = vec3(1.0);
    vec4 closestTint = vec4(0.0);
    vec4 closestOptical = vec4(0.0);
    vec4 closestExtra = vec4(0.0);
    vec2 closestPos = u_positions[0];
    vec2 combinedDir = vec2(0.0, 1.0);
    float combinedDistNorm = 1.0;
    
    bool needReflection = false;
    vec2 dominantReflectionUV = vec2(0.0);
    float dominantReflectionWeight = 0.0;

    if (u_enableZIndex) {
        for (int i = 0; i < 50; i++) {
            if (i >= u_lensCount) break;
            LensResult res = calculateLens(effectUV, u_positions[i], u_lensSizes[i], u_lensSettings[i], u_lensExtra[i]);
            if (res.isInEffect) {
                if (!anyInEffect) {
                    topEdgeAlpha = smoothstep(1.0, 0.985, res.distNorm);
                    anyInEffect = true;
                    closestOptical = u_lensOptical[i];
                    closestExtra = u_lensExtra[i];
                    closestPos = u_positions[i];
                    combinedDir = res.pixelDir;
                    combinedDistNorm = res.distNorm;
                }
                float strength = u_lensSettings[i].y;
                float pinch = u_lensSettings[i].z;
                float effectiveRadius = min(u_lensSizes[i].x, u_lensSizes[i].y) * 0.5;
                float effectivePinch = pinch * (max(effectiveRadius, 1.0) / 100.0);
                float displacementFactor = pow(res.distNorm, effectivePinch);
                float offsetMagnitudePixels = displacementFactor * strength * 40.0;
                
                vec2 offset = res.pixelDir * (offsetMagnitudePixels / u_resolution);
                
                if (u_enableReflections && u_lensOptical[i].y > 0.001 && !needReflection) {
                    vec2 baseNormal = length(res.pixelDir) > 0.0 ? normalize(res.pixelDir) : vec2(0.0, 1.0);
                    vec2 absoluteDir = baseNormal * u_reflectionRayDir;
                    float squeezeStart = mix(0.3, 0.95, clamp(u_reflectionSqueeze, 0.0, 1.0));
                    float rimWidth = 1.0 - squeezeStart;
                    float squeezeProgress = max(0.0, (res.distNorm - squeezeStart) / max(rimWidth, 0.001));
                    float reachFactor = pow(squeezeProgress, 1.5);
                    vec2 reachVector = absoluteDir * (u_reflectionReach / u_resolution) * reachFactor;
                    vec2 warpVector = absoluteDir * (pow(res.distNorm, 3.0) * 0.1 * reachFactor);
                    
                    vec2 smudgeUV = effectUV - offset + reachVector + warpVector;
                    
                    float softEdgeStart = squeezeStart - (u_reflectionRimSoftness * 0.2);
                    float smudgeVisibility = smoothstep(softEdgeStart, squeezeStart + (rimWidth * (0.3 + u_reflectionRimSoftness)), res.distNorm);
                    float distanceOpacity = clamp(1.0 - (reachFactor * u_reflectionFade), 0.0, 1.0);
                    
                    needReflection = true;
                    dominantReflectionUV = clamp(smudgeUV, 0.0, 1.0);
                    dominantReflectionWeight = u_lensOptical[i].y * smudgeVisibility * distanceOpacity;
                }
                
                vec4 tColor = u_tintColors[i];
                if (tColor.a > 0.0) gelColor *= mix(vec3(1.0), tColor.rgb, tColor.a);
                effectUV -= offset;
            }
        }
    } else {
        vec2 totalDistortionOffset = vec2(0.0);

        if (u_mergeMode) {
            float d = mapGlobal(st);
            if (d < 0.0) {
                anyInEffect = true; 
                #ifdef GL_OES_standard_derivatives
                    vec2 normal = normalize(vec2(dFdx(d), dFdy(d)) + 0.00001);
                #else
                    float eps = 1.0;
                    float dx = mapGlobal(st + vec2(eps/u_resolution.x, 0.0)) - d;
                    float dy = mapGlobal(st + vec2(0.0, eps/u_resolution.y)) - d;
                    vec2 normal = normalize(vec2(dx, dy) + 0.00001);
                #endif

                float effectiveRadius = min(u_lensSizes[0].x, u_lensSizes[0].y) * 0.5;
                combinedDistNorm = clamp(1.0 - (abs(d) / effectiveRadius), 0.0, 1.0); 
                combinedDir = normal;
                float strength = u_lensSettings[0].y;
                float pinch = u_lensSettings[0].z;
                
                float minDistForTint = 1e10;
                for (int i = 0; i < 50; i++) {
                    if (i >= u_lensCount) break;
                    float dist = length((st - u_positions[i]) * u_resolution);
                    if (dist < minDistForTint) {
                        minDistForTint = dist;
                        closestTint = u_tintColors[i];
                        closestOptical = u_lensOptical[i];
                        closestExtra = u_lensExtra[i];
                        closestPos = u_positions[i];
                    }
                }
                
                float effectivePinch = pinch * (max(effectiveRadius, 1.0) / 100.0);
                float displacementFactor = pow(combinedDistNorm, effectivePinch);
                float offsetMagnitudePixels = displacementFactor * strength * 40.0;
                totalDistortionOffset = normal * (offsetMagnitudePixels / u_resolution);
            }
        } else {
            float minDistNorm = 1.0; 
            LensResult closestLens; 
            closestLens.isInEffect = false;
            
            for (int i = 0; i < 50; i++) {
                if (i >= u_lensCount) break;
                LensResult res = calculateLens(st, u_positions[i], u_lensSizes[i], u_lensSettings[i], u_lensExtra[i]);
                if (res.isInEffect) {
                    anyInEffect = true;
                    float effectiveRadius = min(u_lensSizes[i].x, u_lensSizes[i].y) * 0.5;
                    float strength = u_lensSettings[i].y;
                    float pinch = u_lensSettings[i].z;
                    float effectivePinch = pinch * (max(effectiveRadius, 1.0) / 100.0);
                    float displacementFactor = pow(res.distNorm, effectivePinch);
                    float offsetMagnitudePixels = displacementFactor * strength * 40.0;
                    totalDistortionOffset += res.pixelDir * (offsetMagnitudePixels / u_resolution);
                    if (res.distNorm < minDistNorm) {
                        minDistNorm = res.distNorm;
                        closestLens = res;
                        closestTint = u_tintColors[i];
                        closestOptical = u_lensOptical[i];
                        closestExtra = u_lensExtra[i];
                        closestPos = u_positions[i];
                    }
                }
            }
            if (anyInEffect) { 
                combinedDistNorm = minDistNorm; 
                combinedDir = closestLens.pixelDir; 
            }
        }

        if (anyInEffect) {
            topEdgeAlpha = smoothstep(1.0, 0.985, combinedDistNorm);
            effectUV = st - totalDistortionOffset;
            if (u_enableReflections && closestOptical.y > 0.001) {
                vec2 baseNormal = length(combinedDir) > 0.0 ? normalize(combinedDir) : vec2(0.0, 1.0); 
                vec2 absoluteDir = baseNormal * u_reflectionRayDir;
                float squeezeStart = mix(0.3, 0.95, clamp(u_reflectionSqueeze, 0.0, 1.0)); 
                float rimWidth = 1.0 - squeezeStart;
                float squeezeProgress = max(0.0, (combinedDistNorm - squeezeStart) / max(rimWidth, 0.001));
                float reachFactor = pow(squeezeProgress, 1.5); 
                vec2 reachVector = absoluteDir * (u_reflectionReach / u_resolution) * reachFactor;
                vec2 warpVector = absoluteDir * (pow(combinedDistNorm, 3.0) * 0.1 * reachFactor);
                
                vec2 smudgeUV = st + reachVector + warpVector;
                
                float softEdgeStart = squeezeStart - (u_reflectionRimSoftness * 0.2);
                float smudgeVisibility = smoothstep(softEdgeStart, squeezeStart + (rimWidth * (0.3 + u_reflectionRimSoftness)), combinedDistNorm);
                float distanceOpacity = clamp(1.0 - (reachFactor * u_reflectionFade), 0.0, 1.0);
                
                needReflection = true;
                dominantReflectionUV = clamp(smudgeUV, 0.0, 1.0);
                dominantReflectionWeight = closestOptical.y * smudgeVisibility * distanceOpacity;
            }
        }
    }

    if (!anyInEffect) {
        discard;
    }

    vec3 accumulatedRims = vec3(0.0);
    if (needReflection && dominantReflectionWeight > 0.001) {
        accumulatedRims = denseSpiralBlur(u_texture, dominantReflectionUV, u_resolution, u_reflectionBlur) * dominantReflectionWeight;
    }

    vec2 finalSampleUV = clamp(effectUV, 0.0, 1.0);
    
    float effectiveZoom = closestExtra.z > 1.001 ? closestExtra.z : (u_zoomAmount > 1.001 ? u_zoomAmount : 1.0);
    if (effectiveZoom > 1.001) {
        vec2 zoomCenter = closestPos;
        finalSampleUV = (finalSampleUV - zoomCenter) / effectiveZoom + zoomCenter;
    }
    
    vec3 bgRGB;
    
    vec2 finalAbOffset = vec2(0.0);
    if (closestExtra.y > 0.001) {
        vec2 dir = length(combinedDir) > 0.0 ? normalize(combinedDir) : vec2(0.0, 1.0);
        finalAbOffset = dir * closestExtra.y * 0.02 * combinedDistNorm;
    }
    
    if (u_enableBackdropBlur && closestOptical.x > 0.001) {
        bgRGB = frostedGlassBlur(u_texture, finalSampleUV, u_resolution, closestOptical.x, finalAbOffset);
    } else {
        vec2 roiUV = mapScreenToRoiUv(finalSampleUV);
        vec2 abOffsetRoi = finalAbOffset / u_roiBounds.zw;
        if (dot(finalAbOffset, finalAbOffset) > 0.000001) {
            bgRGB.r = texture2D(u_texture, clamp(roiUV + abOffsetRoi, 0.0, 1.0)).r;
            bgRGB.g = texture2D(u_texture, roiUV).g;
            bgRGB.b = texture2D(u_texture, clamp(roiUV - abOffsetRoi, 0.0, 1.0)).b;
        } else {
            bgRGB = texture2D(u_texture, roiUV).rgb;
        }
    }
    
    if (u_enableZIndex) {
        bgRGB *= gelColor;
    } else if (closestTint.a > 0.0) {
        bgRGB = mix(bgRGB, closestTint.rgb, closestTint.a);
    }
    bgRGB += accumulatedRims;
    vec3 premultLens = bgRGB * topEdgeAlpha;
    gl_FragColor = vec4(premultLens, topEdgeAlpha);
}
`;
