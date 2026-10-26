const CATALOG_URL = "https://raw.githubusercontent.com/csprofesor/apple/main/data/catalog.json";
const STREAMS_URL = "https://raw.githubusercontent.com/csprofesor/apple/main/data/streams.json";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

function unpackPackerJs(packed) {
    try {
        const match = packed.match(/eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*d?\s*\)\s*\{.*?\}\s*\(\s*['"](.*?)['"]\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*['"](.*?)['"]\.split\(\s*['"]\|['"]\s*\)/s);
        if (!match) return packed;
        const payload = match[1];
        const radix = parseInt(match[2], 10);
        const keywords = match[4].split("|");

        const chars = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
        function decodeBase(str, b) {
            let res = 0;
            for (let i = 0; i < str.length; i++) {
                const idx = chars.indexOf(str[i]);
                if (idx < 0 || idx >= b) return -1;
                res = res * b + idx;
            }
            return res;
        }

        return payload.replace(/\b\w+\b/g, (w) => {
            const idx = decodeBase(w, radix);
            return (idx >= 0 && idx < keywords.length && keywords[idx]) ? keywords[idx] : w;
        }).replace(/\\\//g, "/");
    } catch (_) {
        return packed;
    }
}

async function unpackLiveStreamUrl(rawUrl, referer) {
    if (!rawUrl || rawUrl === "about:blank" || rawUrl.startsWith("about:") || rawUrl.startsWith("javascript:")) {
        return "";
    }

    const lower = rawUrl.toLowerCase();

    // If direct playable stream already, return immediately
    if ((lower.includes(".m3u8") || lower.includes(".mp4")) && !lower.includes("/embed") && !lower.includes("/videoembed") && !lower.includes("/e/")) {
        return rawUrl;
    }

    // 1. Mail.ru unpacker (select 1080p)
    if (lower.includes("my.mail.ru/video/embed/") || lower.includes("my.mail.ru/+/video/meta/")) {
        try {
            const vid = rawUrl.split("/").pop().split("?")[0].replace(".html", "");
            const metaRes = await fetch(`https://my.mail.ru/+/video/meta/${vid}`, {
                headers: { "User-Agent": UA }
            });
            if (metaRes.ok) {
                const metaJson = await metaRes.json();
                if (metaJson && metaJson.videos && metaJson.videos.length > 0) {
                    const best = metaJson.videos.find(v => v.key === "1080p" || v.key === "720p") || metaJson.videos[0];
                    const vUrl = best.url || "";
                    if (vUrl) {
                        return vUrl.startsWith("//") ? `https:${vUrl}` : vUrl;
                    }
                }
            }
        } catch (e) {
            console.error("Mail.ru unpack error:", e);
        }
    }

    try {
        const response = await fetch(rawUrl, {
            headers: {
                "User-Agent": UA,
                "Referer": referer || rawUrl
            }
        });

        if (!response.ok) return "";
        let html = await response.text();

        // Dean Edwards unpack
        if (html.includes("eval(function(p,a,c,k,e,d)")) {
            html += "\n" + unpackPackerJs(html);
        }

        // 2. Bitchute direct MP4
        if (lower.includes("bitchute.com")) {
            const bcMatch = html.match(/(https?:\/\/[^\s"'<>]+\.bitchute\.com\/[^\s"'<>]+\.mp4)/i);
            if (bcMatch && bcMatch[1]) return bcMatch[1];
        }

        // 3. Vidmoly, Vidhide, FileLions, StreamWish, JWPlayer m3u8 regex
        const m3u8Match = html.match(/file\s*:\s*["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i) ||
                          html.match(/sources\s*:\s*\[\s*\{\s*file\s*:\s*["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i) ||
                          html.match(/src\s*:\s*["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i) ||
                          html.match(/["'](https?:\/\/[^"']+\.m3u8[^"']*)["']/i);
        if (m3u8Match && m3u8Match[1]) {
            return m3u8Match[1];
        }

        // 4. VOE regex
        const voeMatch = html.match(/'hls'\s*:\s*['"](https?:\/\/[^'"]+)['"]/i) ||
                         html.match(/["']hls["']\s*:\s*["'](https?:\/\/[^"']+)["']/i);
        if (voeMatch && voeMatch[1]) {
            return voeMatch[1];
        }

        // 5. Ok.ru / Odnoklassniki HLS Manifest
        const okMatch = html.match(/hlsManifestUrl(?:\\?["']|&quot;)\s*:\s*(?:\\?["']|&quot;)(https?:[^"'\s&]+)/i);
        if (okMatch && okMatch[1]) {
            return okMatch[1].replace(/\\u0026/g, "&").replace(/&amp;/g, "&");
        }

        // 6. Streamtape robotlink
        if (lower.includes("streamtape") || lower.includes("streamta.pe")) {
            const linkMatch = html.match(/id=['"](?:robotlink|ideoolink)['"][^>]*>(.*?)</i);
            if (linkMatch && linkMatch[1] && linkMatch[1].startsWith("//")) {
                return "https:" + linkMatch[1];
            }
            const partMatch = html.match(/document\.getElementById\(['"](?:robotlink|ideoolink)['"]\)\.innerHTML\s*=\s*['"]([^'"]+)['"]\s*\+\s*\(['"]([^'"]+)['"]\)\.substring\((\d+)\)/i);
            if (partMatch) {
                const p1 = partMatch[1];
                const p2 = partMatch[2];
                const sub = parseInt(partMatch[3], 10) || 0;
                const combined = (sub < p2.length) ? p1 + p2.substring(sub) : p1 + p2;
                return combined.startsWith("//") ? `https:${combined}` : combined;
            }
        }

        // 7. Sibnet MP4
        if (lower.includes("sibnet.ru")) {
            const sibMatch = html.match(/src\s*:\s*["'](\/v\/[^"']+\.mp4)["']/i);
            if (sibMatch && sibMatch[1]) {
                return "https://video.sibnet.ru" + sibMatch[1];
            }
        }

        // 8. Mixdrop regex
        const mixMatch = html.match(/MDCore\.wurl\s*=\s*["']([^"']+)["']/i);
        if (mixMatch && mixMatch[1]) {
            const u = mixMatch[1];
            return u.startsWith("//") ? `https:${u}` : u;
        }

        // 9. Generic JSON source variable matching (JWPlayer, VideoJS, Plyr)
        const jsonMatch = html.match(/["']?(?:file|src|url|hls|stream)["']?\s*:\s*["'](https?:\/\/[^"']+\.(?:m3u8|mp4)[^"']*)["']/i);
        if (jsonMatch && jsonMatch[1]) {
            return jsonMatch[1];
        }

        // 10. Direct mp4 regex
        const mp4Match = html.match(/["'](https?:\/\/[^"']+\.mp4[^"']*)["']/i);
        if (mp4Match && mp4Match[1]) {
            return mp4Match[1];
        }

        // 11. Generic m3u8 regex
        const anyM3u8 = html.match(/https?:\/\/[^\s"'`<>]+?\.(?:m3u8|mp4)[^\s"'`<>]*/i);
        if (anyM3u8 && anyM3u8[0] && !anyM3u8[0].includes("/thumb") && !anyM3u8[0].includes("/preview")) {
            return anyM3u8[0];
        }
    } catch (e) {
        console.error("unpackLiveStreamUrl error:", e);
    }

    return "";
}

async function getCatalog(type, id, extra) {
    try {
        // 1. Try static category catalog file first (supporting both movie and series directories)
        if (id && id.startsWith("playrepo-") && id !== "playrepo-catalog-movies") {
            try {
                const catFolder = (type === "series" || id.includes("series")) ? "series" : "movie";
                const catFileUrl = `https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/catalog/${catFolder}/${id}.json?t=${Date.now()}`;
                const catRes = await fetch(catFileUrl, {
                    headers: { "User-Agent": UA, "Accept": "application/json" }
                });
                if (catRes.ok) {
                    const catData = await catRes.json();
                    let metas = Array.isArray(catData) ? catData : (catData.metas || []);
                    if (metas && metas.length > 0) {
                        metas.forEach(m => {
                            if (m.poster && m.poster.includes("@")) m.poster = m.poster.replace(/@/g, "%40");
                            if (m.background && m.background.includes("@")) m.background = m.background.replace(/@/g, "%40");
                        });
                        if (extra && extra.search) {
                            const query = extra.search.toLowerCase();
                            metas = metas.filter(m => m.name && m.name.toLowerCase().includes(query));
                        }
                        return { metas: metas };
                    }
                }
            } catch (_) {}
        }

        const response = await fetch(`${CATALOG_URL}?t=${Date.now()}`, {
            headers: {
                "User-Agent": UA,
                "Accept": "application/json"
            }
        });

        if (!response.ok) return { metas: [] };

        const catalogData = await response.json();
        let metas = Array.isArray(catalogData) ? catalogData : (catalogData.metas || []);

        // Filter by Catalog ID (for dedicated Nuvio home screen category rows)
        if (id === "playrepo-aksiyon") {
            metas = metas.filter(m => m.genres && m.genres.some(g => {
                const lg = g.toLowerCase();
                return lg.includes("aksiyon") || lg.includes("macera");
            }));
        } else if (id === "playrepo-komedi") {
            metas = metas.filter(m => m.genres && m.genres.some(g => {
                const lg = g.toLowerCase();
                return lg.includes("komedi") || lg.includes("mizah");
            }));
        } else if (id === "playrepo-dram") {
            metas = metas.filter(m => m.genres && m.genres.some(g => {
                const lg = g.toLowerCase();
                return lg.includes("dram") || lg.includes("romantik");
            }));
        } else if (id === "playrepo-korku") {
            metas = metas.filter(m => m.genres && m.genres.some(g => {
                const lg = g.toLowerCase();
                return lg.includes("korku") || lg.includes("gerilim");
            }));
        } else if (id === "playrepo-bilimkurgu") {
            metas = metas.filter(m => m.genres && m.genres.some(g => {
                const lg = g.toLowerCase();
                return lg.includes("bilim") || lg.includes("kurgu") || lg.includes("fantastik");
            }));
        } else if (id === "playrepo-animasyon") {
            metas = metas.filter(m => m.genres && m.genres.some(g => {
                const lg = g.toLowerCase();
                return lg.includes("animasyon") || lg.includes("çizgi") || lg.includes("anime") || lg.includes("animation");
            }));
        } else if (id === "playrepo-belgesel") {
            metas = metas.filter(m => m.genres && m.genres.some(g => {
                const lg = g.toLowerCase();
                return lg.includes("belgesel") || lg.includes("tarih") || lg.includes("biyografi") || lg.includes("documentary") || lg.includes("history");
            }));
        } else if (id === "playrepo-suc") {
            metas = metas.filter(m => m.genres && m.genres.some(g => {
                const lg = g.toLowerCase();
                return lg.includes("suç") || lg.includes("suc") || lg.includes("polisiye") || lg.includes("gizem") || lg.includes("crime") || lg.includes("mystery");
            }));
        } else if (id === "playrepo-series-popular") {
            metas = metas.filter(m => m.type === "series");
        }

        if (extra && extra.genre) {
            const requestedGenre = extra.genre.toLowerCase();
            metas = metas.filter(m => {
                if (!m.genres) return true;
                return m.genres.some(g => g.toLowerCase().includes(requestedGenre) || requestedGenre.includes(g.toLowerCase()));
            });
        }

        if (extra && extra.search) {
            const query = extra.search.toLowerCase();
            metas = metas.filter(m => m.name && m.name.toLowerCase().includes(query));
        }

        metas.forEach(m => {
            if (m.poster && m.poster.includes("@")) m.poster = m.poster.replace(/@/g, "%40");
            if (m.background && m.background.includes("@")) m.background = m.background.replace(/@/g, "%40");
        });

        return { metas: metas };
    } catch (e) {
        console.error("Play getCatalog error:", e);
        return { metas: [] };
    }
}

async function getMeta(arg1, arg2) {
    try {
        let mediaType = "movie";
        let idStr = "";

        if (typeof arg1 === "object" && arg1 !== null) {
            idStr = (arg1.id || arg1.tmdbId || "").toString();
            mediaType = arg1.type || arg1.mediaType || "movie";
        } else if (arg1 === "movie" || arg1 === "series" || arg1 === "tv") {
            mediaType = arg1;
            idStr = arg2 ? arg2.toString() : "";
        } else {
            idStr = arg1 ? arg1.toString() : "";
            mediaType = arg2 || "movie";
        }

        if (!idStr) return { meta: null };

        const cleanId = idStr.replace(/^tmdb:/, "").replace(/^tmdb-/, "").replace(/^movie\//, "").replace(/^tv\//, "").replace(/^playrepo:/, "").replace(/^playrepo-/, "");
        const safeId = idStr.replace(/:/g, "_");
        const hyphenId = idStr.replace(/:/g, "-").replace(/_/g, "-");

        // 1. Try static meta file published by APK first (checking all safe variations)
        const possibleStaticIds = [encodeURIComponent(idStr), encodeURIComponent(hyphenId), encodeURIComponent(safeId), encodeURIComponent(cleanId)];
        const targetTypes = mediaType === "series" ? ["series", "movie"] : ["movie", "series"];
        for (const t of targetTypes) {
            for (const pid of possibleStaticIds) {
                try {
                    const staticMetaUrl = `https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/meta/${t}/${pid}.json?t=${Date.now()}`;
                    const smRes = await fetch(staticMetaUrl, {
                        headers: { "User-Agent": UA, "Accept": "application/json" }
                    });
                    if (smRes.ok) {
                        const smData = await smRes.json();
                        if (smData && smData.meta) {
                            if (smData.meta.poster && smData.meta.poster.includes("@")) smData.meta.poster = smData.meta.poster.replace(/@/g, "%40");
                            if (smData.meta.background && smData.meta.background.includes("@")) smData.meta.background = smData.meta.background.replace(/@/g, "%40");
                            return smData;
                        }
                    }
                } catch (_) {}
            }
        }

        // 2. Search local catalog (Contains site's official Turkish description, genres, and poster)
        try {
            const response = await fetch(`${CATALOG_URL}?t=${Date.now()}`, {
                headers: {
                    "User-Agent": UA,
                    "Accept": "application/json"
                }
            });

            if (response.ok) {
                const catalogData = await response.json();
                const metas = Array.isArray(catalogData) ? catalogData : (catalogData.metas || []);

                const normTarget = idStr.toLowerCase().replace(/[:_]/g, "-");
                const found = metas.find(m => {
                    if (!m || !m.id) return false;
                    const normM = m.id.toLowerCase().replace(/[:_]/g, "-");
                    return normM === normTarget || m.id === idStr || m.id === hyphenId || m.id === safeId || m.id === cleanId || normM.endsWith(normTarget) || normTarget.endsWith(normM);
                });

                if (found) {
                    const videoId = found.id || idStr;
                    const metaObj = {
                        id: videoId,
                        type: found.type || "movie",
                        name: found.name,
                        poster: found.poster || "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                        background: found.background || found.poster || "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                        logo: "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                        description: found.description || "Play • Film Yayın Kaynağı",
                        genres: found.genres || ["Popüler"],
                        releaseInfo: found.releaseInfo || "",
                        imdbRating: found.imdbRating || "",
                        runtime: found.runtime || "",
                        behaviorHints: {
                            defaultVideoId: videoId,
                            hasScheduledVideos: false
                        },
                        videos: [
                            {
                                id: videoId,
                                title: found.name,
                                released: found.releaseInfo ? `${found.releaseInfo}-01-01T00:00:00.000Z` : undefined
                            }
                        ]
                    };

                    // If it has IMDb ID, enrich with trailer/cast from Cinemeta but PRESERVE Turkish description and genres
                    if (idStr.startsWith("tt")) {
                        try {
                            const cmRes = await fetch(`https://v3-cinemeta.strem.io/meta/movie/${idStr}.json`, {
                                headers: { "User-Agent": UA }
                            });
                            if (cmRes.ok) {
                                const cmData = await cmRes.json();
                                if (cmData && cmData.meta) {
                                    if (cmData.meta.cast) metaObj.cast = cmData.meta.cast;
                                    if (cmData.meta.director) metaObj.director = cmData.meta.director;
                                    if (cmData.meta.trailers) metaObj.trailers = cmData.meta.trailers;
                                    if (cmData.meta.trailerStreams) metaObj.trailerStreams = cmData.meta.trailerStreams;
                                }
                            }
                        } catch (_) {}
                    }

                    return { meta: metaObj };
                }
            }
        } catch (_) {}

        // 3. Fallback: Cinemeta if not found in catalog yet
        if (idStr.startsWith("tt")) {
            try {
                const cType = mediaType === "series" ? "series" : "movie";
                const cmRes = await fetch(`https://v3-cinemeta.strem.io/meta/${cType}/${idStr}.json`, {
                    headers: { "User-Agent": UA }
                });
                if (cmRes.ok) {
                    const cmData = await cmRes.json();
                    if (cmData && cmData.meta) {
                        const m = cmData.meta;
                        if (!m.behaviorHints) m.behaviorHints = {};
                        m.behaviorHints.defaultVideoId = idStr;
                        m.behaviorHints.hasScheduledVideos = false;
                        if (!m.videos || m.videos.length === 0) {
                            m.videos = [
                                {
                                    id: idStr,
                                    title: m.name,
                                    released: m.releaseInfo ? `${m.releaseInfo}-01-01T00:00:00.000Z` : undefined
                                }
                            ];
                        }
                        return { meta: m };
                    }
                }
            } catch (_) { }
        }

        const fallbackId = idStr || cleanId;
        return {
            meta: {
                id: fallbackId,
                type: mediaType || "movie",
                name: `Film ${cleanId}`,
                poster: "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                background: "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                logo: "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
                description: "Play • Film Yayın Kaynağı",
                genres: ["Popüler"],
                behaviorHints: {
                    defaultVideoId: fallbackId,
                    hasScheduledVideos: false
                },
                videos: [
                    {
                        id: fallbackId,
                        title: `Film ${cleanId}`
                    }
                ]
            }
        };
    } catch (e) {
        console.error("Play getMeta error:", e);
        return { meta: null };
    }
}

async function getStreams(arg1, arg2, arg3, arg4) {
    try {
        let idStr = "";
        let mediaType = "movie";
        let season = 1;
        let episode = 1;

        if (arg1 === "movie" || arg1 === "series" || arg1 === "tv") {
            mediaType = arg1;
            idStr = arg2 ? arg2.toString() : "";
            season = arg3 || 1;
            episode = arg4 || 1;
        } else {
            idStr = arg1 ? arg1.toString() : "";
            mediaType = arg2 || "movie";
            season = arg3 || 1;
            episode = arg4 || 1;
        }

        if (!idStr) return [];
        const cleanId = idStr.replace(/^tmdb:/, "").replace(/^tmdb-/, "").replace(/^movie\//, "").replace(/^tv\//, "").replace(/^playrepo:/, "").replace(/^playrepo-/, "");
        const safeId = idStr.replace(/:/g, "_");
        const hyphenId = idStr.replace(/:/g, "-").replace(/_/g, "-");

        // 1. Try static stream file published by APK first (movie or series)
        const possibleStaticIds = [encodeURIComponent(idStr), encodeURIComponent(hyphenId), encodeURIComponent(safeId), encodeURIComponent(cleanId)];
        const folderType = (mediaType === "series" || idStr.includes(":")) ? "series" : "movie";
        for (const pid of possibleStaticIds) {
            try {
                const staticStreamUrl = `https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/stream/${folderType}/${pid}.json?t=${Date.now()}`;
                const ssRes = await fetch(staticStreamUrl, {
                    headers: { "User-Agent": UA, "Accept": "application/json" }
                });
                if (ssRes.ok) {
                    const ssData = await ssRes.json();
                    if (ssData && ssData.streams && ssData.streams.length > 0) {
                        const sorted = ssData.streams.sort((a, b) => {
                            const aHls = (a.format === "m3u8" || (a.url && a.url.includes(".m3u8"))) ? 1 : 0;
                            const bHls = (b.format === "m3u8" || (b.url && b.url.includes(".m3u8"))) ? 1 : 0;
                            return bHls - aHls;
                        });
                        return sorted.map((st, idx) => {
                            const ref = (st.behaviorHints && st.behaviorHints.proxyHeaders && st.behaviorHints.proxyHeaders.request && st.behaviorHints.proxyHeaders.request.Referer) || st.referer || st.url;
                            const isM3u8 = (st.url && st.url.includes(".m3u8")) || st.format === "m3u8";
                            return {
                                ...st,
                                format: isM3u8 ? "m3u8" : "mp4",
                                title: st.title || `Play • Sunucu ${idx + 1}`,
                                headers: {
                                    "User-Agent": UA,
                                    "Referer": ref
                                }
                            };
                        });
                    }
                }
            } catch (_) {}
        }

        // 2. Fetch streams.json
        const response = await fetch(`${STREAMS_URL}?t=${Date.now()}`, {
            headers: {
                "User-Agent": UA,
                "Accept": "application/json"
            }
        });

        if (!response.ok) return [];

        const data = await response.json();
        const normTarget = idStr.toLowerCase().replace(/[:_]/g, "-");

        let itemStreams = data[idStr] || data[hyphenId] || data[safeId] || data[cleanId] || data[`movie/${cleanId}`] || data[`tmdb:${cleanId}`] || data[`playrepo:${cleanId}`] || [];

        if (!itemStreams || itemStreams.length === 0) {
            for (const k of Object.keys(data)) {
                const normK = k.toLowerCase().replace(/[:_]/g, "-");
                if (normK === normTarget || normK.endsWith(normTarget) || normTarget.endsWith(normK)) {
                    itemStreams = data[k];
                    break;
                }
            }
        }

        if (!itemStreams || itemStreams.length === 0) return [];

        const resolvedPromises = itemStreams.map(async (s) => {
            const refererUrl = s.referer || s.url || "https://www.google.com";
            const targetUrl = (refererUrl.includes("/embed") || refererUrl.includes("/videoembed") || refererUrl.includes("/e/") || s.url.includes("srcIp=")) ? refererUrl : s.url;
            let directUrl = await unpackLiveStreamUrl(targetUrl, refererUrl);
            if (!directUrl) directUrl = s.url;

            if (!directUrl) return null;
            const lowerDirect = directUrl.toLowerCase();

            if (!lowerDirect.includes(".m3u8") && !lowerDirect.includes(".mp4") && !lowerDirect.includes("/manifest")) {
                return null;
            }

            const isM3u8 = lowerDirect.includes(".m3u8");
            const rawTitle = s.title || `Play • ${s.quality || '1080p'}`;
            const maskedTitle = rawTitle.replace(/^PlayRepo/i, "Play");

            return {
                name: "Play",
                title: maskedTitle,
                url: directUrl,
                quality: s.quality || 1080,
                provider: "play",
                format: isM3u8 ? "m3u8" : "mp4",
                behaviorHints: {
                    notSupported: false,
                    proxyHeaders: {
                        "request": {
                            "User-Agent": UA,
                            "Referer": refererUrl
                        }
                    }
                },
                headers: {
                    "User-Agent": UA,
                    "Referer": refererUrl
                }
            };
        });

        const resolvedStreams = await Promise.all(resolvedPromises);
        const validStreams = resolvedStreams.filter(s => s !== null && s.url && s.url.length > 5);
        validStreams.sort((a, b) => {
            const aHls = (a.format === "m3u8" || (a.url && a.url.includes(".m3u8"))) ? 1 : 0;
            const bHls = (b.format === "m3u8" || (b.url && b.url.includes(".m3u8"))) ? 1 : 0;
            return bHls - aHls;
        });
        return validStreams;
    } catch (e) {
        console.error("Play getStreams error:", e);
        return [];
    }
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        getCatalog: getCatalog,
        catalog: getCatalog,
        getMeta: getMeta,
        meta: getMeta,
        getStreams: getStreams,
        stream: getStreams
    };
}
if (typeof window !== "undefined") {
    window.getStreams = getStreams;
    window.getCatalog = getCatalog;
    window.getMeta = getMeta;
}
if (typeof global !== "undefined") {
    global.getStreams = getStreams;
    global.getCatalog = getCatalog;
    global.getMeta = getMeta;
}
if (typeof globalThis !== "undefined") {
    globalThis.getStreams = getStreams;
    globalThis.getCatalog = getCatalog;
    globalThis.getMeta = getMeta;
}
