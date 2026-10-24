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
        const cleanId = tmdbId ? tmdbId.toString().replace("tmdb:", "") : "";

        const keyWithPrefix = mediaType === "tv"
            ? `tv/${cleanId}/${season || 1}/${episode || 1}`
            : `movie/${cleanId}`;

        const itemStreams = data[keyWithPrefix] || data[cleanId] || data[tmdbId?.toString()] || [];

        return itemStreams.map((s) => ({
            name: s.name || "PlayRepo",
            title: s.title || `PlayRepo • ${s.quality || '1080p'}`,
            url: s.url,
            quality: s.quality || 1080,
            provider: "playrepo-main",
            format: s.format || "m3u8",
            headers: s.headers || {
                "User-Agent": UA,
                "Referer": s.referer || s.url
            }
        }));
    } catch (e) {
        console.error("PlayRepo getStreams error:", e);
        return [];
    }
}

module.exports = {
    getStreams: getStreams
};
