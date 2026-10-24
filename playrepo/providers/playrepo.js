const STREAMS_URL = "https://raw.githubusercontent.com/csprofesor/apple/main/data/streams.json";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

async function getStreams(tmdbId, mediaType, season, episode) {
    try {
        const response = await fetch(STREAMS_URL, {
            headers: {
                "User-Agent": UA,
                "Accept": "application/json"
            }
        });

        if (!response.ok) return [];

        const data = await response.json();
        const idStr = tmdbId ? tmdbId.toString() : "";
        const cleanId = idStr.replace(/^tmdb:/, "").replace(/^movie\//, "").replace(/^tv\//, "");

        const keyMovie = `movie/${cleanId}`;
        const keyTv = `tv/${cleanId}/${season || 1}/${episode || 1}`;

        const itemStreams = data[keyTv] || data[keyMovie] || data[cleanId] || data[idStr] || [];

        return itemStreams.map((s) => {
            const refererUrl = s.referer || s.url || "https://www.google.com";
            return {
                name: s.name || "PlayRepo",
                title: s.title || `PlayRepo • ${s.quality || '1080p'}`,
                url: s.url,
                quality: s.quality || 1080,
                provider: "playrepo-main",
                format: s.format || "m3u8",
                behaviorHints: {
                    notSupported: false,
                    requestHeaders: {
                        "User-Agent": UA,
                        "Referer": refererUrl
                    }
                },
                headers: {
                    "User-Agent": UA,
                    "Referer": refererUrl
                }
            };
        });
    } catch (e) {
        console.error("PlayRepo getStreams error:", e);
        return [];
    }
}

module.exports = {
    getStreams: getStreams
};
