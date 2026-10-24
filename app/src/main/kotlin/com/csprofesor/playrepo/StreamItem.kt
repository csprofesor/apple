package com.csprofesor.playrepo

data class StreamItem(
    val name: String = "PlayRepo",
    val title: String,
    val url: String,
    val quality: Int = 1080,
    val format: String = "m3u8",
    val referer: String? = null
)

data class ScrapedMovie(
    val tmdbId: String,
    val title: String,
    val poster: String = "https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/logo.png",
    val streams: List<StreamItem>,
    val genres: List<String> = listOf("Popüler")
)
