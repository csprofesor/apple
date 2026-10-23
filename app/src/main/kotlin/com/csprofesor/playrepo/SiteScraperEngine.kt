package com.csprofesor.playrepo

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.jsoup.Jsoup
import java.util.concurrent.TimeUnit

class SiteScraperEngine {

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .followRedirects(true)
        .build()

    private val userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    suspend fun scrapeUrl(movieUrl: String, customTitle: String? = null, tmdbIdInput: String? = null): ScrapedMovie = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(movieUrl)
            .header("User-Agent", userAgent)
            .build()

        val response = client.newCall(request).execute()
        val html = response.body?.string().orEmpty()
        val doc = Jsoup.parse(html, movieUrl)

        val title = customTitle?.takeIf { it.isNotBlank() }
            ?: doc.selectFirst("h1")?.text()?.trim()
            ?: doc.selectFirst("meta[property=og:title]")?.attr("content")?.trim()
            ?: "Bilinmeyen Film"

        val foundStreams = mutableListOf<StreamItem>()

        // 1. Scrape direct iframe & video tags
        doc.select("iframe[src], video[src], video source[src]").forEach { el ->
            val src = el.absUrl("src").ifBlank { el.attr("src") }
            if (src.isNotBlank()) {
                val resolvedStream = resolveStreamUrl(src, movieUrl)
                if (resolvedStream != null) {
                    foundStreams.add(resolvedStream)
                }
            }
        }

        // 2. Scrape external links (vidmoly, vidhide, voe, streamtape, ok.ru)
        doc.select("a[href]").forEach { a ->
            val href = a.absUrl("href").ifBlank { a.attr("href") }
            if (href.contains("vidmoly") || href.contains("vidhide") ||
                href.contains("voe.sx") || href.contains("streamtape") ||
                href.contains("ok.ru") || href.contains("kinescope")
            ) {
                val resolvedStream = resolveStreamUrl(href, movieUrl)
                if (resolvedStream != null && foundStreams.none { it.url == resolvedStream.url }) {
                    foundStreams.add(resolvedStream)
                }
            }
        }

        // Fallback: If no iframe stream found, add the movie detail URL as direct source
        if (foundStreams.isEmpty()) {
            foundStreams.add(
                StreamItem(
                    name = "PlayRepo Direct",
                    title = "$title • Direct Web Source",
                    url = movieUrl,
                    quality = 1080,
                    format = "m3u8",
                    referer = movieUrl
                )
            )
        }

        val finalTmdbId = tmdbIdInput?.takeIf { it.isNotBlank() } ?: "550"

        ScrapedMovie(
            tmdbId = finalTmdbId,
            title = title,
            streams = foundStreams
        )
    }

    private fun resolveStreamUrl(embedUrl: String, referer: String): StreamItem? {
        val format = if (embedUrl.contains(".m3u8")) "m3u8" else "mp4"
        val providerName = when {
            embedUrl.contains("vidmoly", true) -> "Vidmoly"
            embedUrl.contains("vidhide", true) -> "Vidhide"
            embedUrl.contains("kinescope", true) -> "Kinescope"
            embedUrl.contains("voe", true) -> "VOE"
            embedUrl.contains("streamtape", true) -> "Streamtape"
            else -> "PlayRepo Server"
        }

        return StreamItem(
            name = "PlayRepo • $providerName",
            title = "PlayRepo • $providerName • 1080p",
            url = embedUrl,
            quality = 1080,
            format = format,
            referer = referer
        )
    }
}
