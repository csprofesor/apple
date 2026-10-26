package com.csprofesor.playrepo

data class SubtitleItem(
    val id: String = "tr",
    val url: String,
    val lang: String = "Turkish"
)

data class StreamProxyHeaders(
    val request: Map<String, String>
)

data class StreamBehaviorHints(
    val notSupported: Boolean = false,
    val proxyHeaders: StreamProxyHeaders? = null
)

data class StreamItem(
    val name: String = "Play",
    val title: String,
    val url: String,
    val quality: Int = 1080,
    val format: String = "m3u8",
    val referer: String? = null,
    val headers: Map<String, String>? = null,
    val behaviorHints: StreamBehaviorHints? = null,
    val subtitles: List<SubtitleItem>? = null
)

data class ScrapedMovie(
    val tmdbId: String,
    val title: String,
    val poster: String = "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png",
    val streams: List<StreamItem>,
    val genres: List<String> = listOf("Popüler"),
    val description: String? = null,
    val year: String? = null,
    val imdbRating: String? = null,
    val runtime: String? = null,
    val director: String? = null,
    val cast: List<String>? = null,
    val type: String = "movie",
    val season: Int? = null,
    val episode: Int? = null
)
