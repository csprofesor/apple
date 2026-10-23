package com.csprofesor.playrepo

import com.google.gson.Gson
import com.google.gson.JsonObject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.jsoup.Jsoup
import java.net.URLEncoder
import java.util.concurrent.TimeUnit
import kotlin.random.Random

class SiteScraperEngine {

    private val client = OkHttpClient.Builder()
        .connectTimeout(12, TimeUnit.SECONDS)
        .readTimeout(12, TimeUnit.SECONDS)
        .followRedirects(true)
        .build()

    private val gson = Gson()

    private val userAgents = listOf(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"
    )

    private fun getRandomUserAgent(): String = userAgents[Random.nextInt(userAgents.size)]

    /**
     * Finds ALL movie page links dynamically across all catalog pages.
     */
    suspend fun discoverAllMovieLinks(
        siteUrl: String,
        maxPages: Int = 30,
        onPageProgress: suspend (pageNumber: Int, foundMoviesCount: Int, statusMsg: String) -> Unit
    ): List<String> = withContext(Dispatchers.IO) {
        val siteBase = if (siteUrl.contains("://")) {
            val parts = siteUrl.split("/")
            "${parts[0]}//${parts[2]}"
        } else siteUrl

        val cleanSiteUrl = siteUrl.trimEnd('/')
        val discoveredMovies = mutableSetOf<String>()
        val visitedCatalogPages = mutableSetOf<String>()
        val catalogPagesQueue = mutableListOf<String>()

        catalogPagesQueue.add(siteUrl)
        if (!cleanSiteUrl.endsWith("/film")) {
            catalogPagesQueue.add("$siteBase/film/")
        }

        var pageIndex = 0

        while (catalogPagesQueue.isNotEmpty() && pageIndex < maxPages) {
            val currentPageUrl = catalogPagesQueue.removeAt(0)
            if (visitedCatalogPages.contains(currentPageUrl)) continue
            visitedCatalogPages.add(currentPageUrl)

            pageIndex++

            try {
                val request = Request.Builder()
                    .url(currentPageUrl)
                    .header("User-Agent", getRandomUserAgent())
                    .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
                    .header("Accept-Language", "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7")
                    .build()

                client.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        onPageProgress(pageIndex, discoveredMovies.size, "Sayfa $pageIndex es geçildi (${response.code})")
                        return@use
                    }

                    val html = response.body?.string().orEmpty()
                    val doc = Jsoup.parse(html, currentPageUrl)

                    var newMoviesInPage = 0
                    doc.select("a[href]").forEach { a ->
                        val href = a.absUrl("href").trim()
                        if (isValidMovieUrl(href, siteBase)) {
                            if (discoveredMovies.add(href)) {
                                newMoviesInPage++
                            }
                        }
                    }

                    onPageProgress(pageIndex, discoveredMovies.size, "Sayfa $pageIndex taranıyor ($newMoviesInPage yeni film eklendi)")

                    doc.select("a[href]").forEach { a ->
                        val href = a.absUrl("href").trim()
                        if (isValidCatalogPageUrl(href, siteBase) && !visitedCatalogPages.contains(href) && !catalogPagesQueue.contains(href)) {
                            catalogPagesQueue.add(href)
                        }
                    }
                }
            } catch (e: Exception) {
                onPageProgress(pageIndex, discoveredMovies.size, "Sayfa $pageIndex hatası: ${e.localizedMessage ?: "Bağlantı kesildi"}")
            }

            delay(50)
        }

        discoveredMovies.toList()
    }

    private fun isValidMovieUrl(url: String, siteBase: String): Boolean {
        if (!url.startsWith(siteBase)) return false
        val lower = url.lowercase()

        if (url == siteBase || url == "$siteBase/" ||
            lower.contains("/kategori/") || lower.contains("/tur/") ||
            lower.contains("/koleksiyon/") || lower.contains("/forum") ||
            lower.contains("/iletisim") || lower.contains("/page/") ||
            lower.contains("page=") || lower.contains("orderby=") ||
            lower.contains("/tag/") || lower.contains("#") ||
            lower.contains("javascript:") || lower.endsWith(".jpg") ||
            lower.endsWith(".png") || lower.endsWith(".css") || lower.endsWith(".js")
        ) {
            return false
        }

        return lower.contains("/film/") || lower.contains("/diziler/") || lower.contains("/dizi/") || lower.contains("-izle")
    }

    private fun isValidCatalogPageUrl(url: String, siteBase: String): Boolean {
        if (!url.startsWith(siteBase)) return false
        val lower = url.lowercase()
        if (isValidMovieUrl(url, siteBase)) return false

        return lower.contains("/page/") || lower.contains("page=") ||
               lower.contains("/kategori/") || lower.contains("/tur/") ||
               lower == "$siteBase/film/" || lower == "$siteBase/film" ||
               lower.contains("orderby=")
    }

    /**
     * Crawls all movie links across the whole site and resolves details & stream URLs for each movie.
     */
    suspend fun crawlEntireSite(
        siteUrl: String,
        maxPages: Int = 30,
        tmdbApiKey: String? = null,
        onStatusUpdate: suspend (status: String) -> Unit
    ): List<ScrapedMovie> = withContext(Dispatchers.IO) {
        onStatusUpdate("Sitedeki tüm sayfalar taranıyor (Kategoriler ve Sayfalandırmalar çekiliyor)...")

        val allMovieUrls = discoverAllMovieLinks(
            siteUrl = siteUrl,
            maxPages = maxPages,
            onPageProgress = { pageNum, count, msg ->
                onStatusUpdate("[$pageNum / $maxPages] $msg\nToplam Benzersiz Film: $count")
            }
        )

        if (allMovieUrls.isEmpty()) {
            onStatusUpdate("✗ Sitede film bağlantısı bulunamadı! Lütfen adresi kontrol edin.")
            return@withContext emptyList()
        }

        onStatusUpdate("✓ Toplam ${allMovieUrls.size} benzersiz film tespit edildi! Video kaynakları ve şifrelemeler çözülüyor...")

        val results = mutableListOf<ScrapedMovie>()

        allMovieUrls.forEachIndexed { index, url ->
            try {
                val movie = scrapeMoviePage(url, tmdbApiKey)
                if (movie != null) {
                    results.add(movie)
                    onStatusUpdate("[${index + 1} / ${allMovieUrls.size}] Çözüldü: ${movie.title} (${movie.streams.size} M3U8/MP4 yayın kaynağı)")
                }
            } catch (e: Exception) {
                onStatusUpdate("[${index + 1} / ${allMovieUrls.size}] Sayfa hatası: ${e.localizedMessage}")
            }
        }

        results
    }

    /**
     * Scrapes a single movie page, unpacks iframe embeds into direct M3U8/MP4 stream URLs, and resolves TMDB ID.
     */
    suspend fun scrapeMoviePage(movieUrl: String, tmdbApiKey: String? = null): ScrapedMovie? = withContext(Dispatchers.IO) {
        val request = Request.Builder()
            .url(movieUrl)
            .header("User-Agent", getRandomUserAgent())
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) return@withContext null

            val html = response.body?.string().orEmpty()
            val doc = Jsoup.parse(html, movieUrl)

            val rawTitle = doc.selectFirst("h1")?.text()?.trim()
                ?: doc.selectFirst("meta[property=og:title]")?.attr("content")?.trim()
                ?: movieUrl.substringAfterLast("/").replace("-", " ")

            val cleanTitle = cleanMovieTitle(rawTitle)
            if (cleanTitle.isBlank()) return@withContext null

            val isDub = html.contains("dublaj", true) || rawTitle.contains("dublaj", true)
            val langTag = if (isDub) "[TR Dublaj]" else "[TR Altyazılı]"
            val qualityTag = if (html.contains("4k", true) || rawTitle.contains("4k", true)) "4K" else "1080p"

            val foundStreams = mutableListOf<StreamItem>()

            // 1. Scrape direct iframe & video tags
            doc.select("iframe[src], video[src], video source[src]").forEach { el ->
                val src = el.absUrl("src").ifBlank { el.attr("src") }
                if (src.isNotBlank() && !src.contains("about:blank")) {
                    val resolvedStream = resolveStreamUrl(src, movieUrl, foundStreams.size + 1, qualityTag, langTag)
                    foundStreams.add(resolvedStream)
                }
            }

            // 2. Scrape data-src / data-url / data-player / data-embed attributes from player elements
            doc.select("[data-src], [data-url], [data-player], [data-embed]").forEach { el ->
                val dataSrc = el.attr("data-src").ifBlank { el.attr("data-url") }.ifBlank { el.attr("data-player") }.ifBlank { el.attr("data-embed") }
                if (dataSrc.isNotBlank() && !dataSrc.contains("about:blank")) {
                    val fullUrl = if (dataSrc.startsWith("http")) dataSrc else if (dataSrc.startsWith("//")) "https:$dataSrc" else movieUrl
                    if (foundStreams.none { it.url == fullUrl }) {
                        val resolvedStream = resolveStreamUrl(fullUrl, movieUrl, foundStreams.size + 1, qualityTag, langTag)
                        foundStreams.add(resolvedStream)
                    }
                }
            }

            // 3. Scrape external links (vidmoly, vidhide, voe, streamtape, ok.ru, kinescope, odnoklassniki, mixdrop, doodstream, vidsrc, rapidrame)
            doc.select("a[href]").forEach { a ->
                val href = a.absUrl("href").ifBlank { a.attr("href") }
                if (href.contains("vidmoly") || href.contains("vidhide") || href.contains("filelions") ||
                    href.contains("streamwish") || href.contains("voe.sx") || href.contains("streamtape") ||
                    href.contains("ok.ru") || href.contains("kinescope") || href.contains("odnoklassniki") ||
                    href.contains("mixdrop") || href.contains("dood") || href.contains("vidsrc") || href.contains("vixsrc")
                ) {
                    val resolvedStream = resolveStreamUrl(href, movieUrl, foundStreams.size + 1, qualityTag, langTag)
                    if (foundStreams.none { it.url == resolvedStream.url }) {
                        foundStreams.add(resolvedStream)
                    }
                }
            }

            // 4. Scrape Base64 encoded atob("...") player embeds
            val atobRegex = Regex("""atob\s*\(\s*["'`]([A-Za-z0-9+/=]+)["'`]\s*\)""")
            atobRegex.findAll(html).forEach { match ->
                try {
                    val b64 = match.groupValues[1]
                    val decodedBytes = android.util.Base64.decode(b64, android.util.Base64.DEFAULT)
                    val decodedJson = String(decodedBytes, Charsets.UTF_8).replace("\\/", "/")
                    val urlRegex = Regex("""https?://[^\s"'\\]+""")
                    urlRegex.findAll(decodedJson).forEach { urlMatch ->
                        val embedUrl = urlMatch.value
                        if (embedUrl.isNotBlank() && foundStreams.none { it.url == embedUrl }) {
                            val resolvedStream = resolveStreamUrl(embedUrl, movieUrl, foundStreams.size + 1, qualityTag, langTag)
                            foundStreams.add(resolvedStream)
                        }
                    }
                } catch (_: Exception) { }
            }

            // Fallback: If no embeds found, use movie page URL
            if (foundStreams.isEmpty()) {
                foundStreams.add(
                    StreamItem(
                        name = "PlayRepo",
                        title = "PlayRepo • Sunucu 1 • $qualityTag • $langTag",
                        url = movieUrl,
                        quality = if (qualityTag == "4K") 2160 else 1080,
                        format = "m3u8",
                        referer = movieUrl
                    )
                )
            }

            // Resolve TMDB ID
            val tmdbId = resolveTmdbId(cleanTitle, tmdbApiKey)

            ScrapedMovie(
                tmdbId = tmdbId,
                title = cleanTitle,
                streams = foundStreams
            )
        }
    }

    private fun cleanMovieTitle(title: String): String {
        return title
            .removeSuffix(" izle")
            .removeSuffix(" İzle")
            .removeSuffix(" Türkçe Dublaj")
            .removeSuffix(" Türkçe Altyazılı")
            .removeSuffix(" Full HD")
            .removeSuffix(" 1080p")
            .replace(Regex("""\b(19|20)\d{2}\b.*"""), "")
            .trim()
    }

    private fun resolveStreamUrl(embedUrl: String, referer: String, serverIndex: Int, qualityTag: String, langTag: String): StreamItem {
        val directStreamUrl = unpackEmbedStreamUrl(embedUrl, referer)
        val format = if (directStreamUrl.contains(".m3u8")) "m3u8" else "mp4"

        return StreamItem(
            name = "PlayRepo",
            title = "PlayRepo • Sunucu $serverIndex • $qualityTag • $langTag",
            url = directStreamUrl,
            quality = if (qualityTag == "4K") 2160 else 1080,
            format = format,
            referer = embedUrl
        )
    }

    /**
     * Unpacks/decrypts embed players (Vidmoly, Vidhide, FileLions, StreamWish, VOE, Kinescope, Streamtape, Ok.ru, Mixdrop, Doodstream, Vidsrc) into direct M3U8 / MP4 URLs.
     */
    private fun unpackEmbedStreamUrl(embedUrl: String, referer: String): String {
        if (embedUrl.contains(".m3u8") && !embedUrl.contains("/embed/")) {
            return embedUrl
        }

        try {
            val request = Request.Builder()
                .url(embedUrl)
                .header("User-Agent", getRandomUserAgent())
                .header("Referer", referer)
                .build()

            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return embedUrl
                val html = response.body?.string().orEmpty()

                // 1. Vidmoly, Vidhide, FileLions, StreamWish, JWPlayer, Standard M3U8 regex
                val m3u8Match = Regex("""file\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                    ?: Regex("""src\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                    ?: Regex("""["'](https?://[^"']+\.m3u8[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                if (m3u8Match != null) {
                    return m3u8Match.groupValues[1]
                }

                // 2. VOE regex: 'hls': '(https://...m3u8)'
                val voeMatch = Regex("""'hls'\s*:\s*['"](https?://[^'"]+)['"]""", RegexOption.IGNORE_CASE).find(html)
                    ?: Regex("""["']hls["']\s*:\s*["'](https?://[^"']+)["']""", RegexOption.IGNORE_CASE).find(html)
                if (voeMatch != null) {
                    return voeMatch.groupValues[1]
                }

                // 3. Ok.ru (Odnoklassniki) HLS Manifest
                val okRuMatch = Regex("""hlsManifestUrl["']?\s*:\s*["'](https?://[^"']+)["']""", RegexOption.IGNORE_CASE).find(html)
                if (okRuMatch != null) {
                    return okRuMatch.groupValues[1].replace("\\u0026", "&")
                }

                // 4. Mixdrop regex: MDCore.wurl="(https://...)"
                val mixdropMatch = Regex("""MDCore\.wurl\s*=\s*["']([^"']+)["']""", RegexOption.IGNORE_CASE).find(html)
                if (mixdropMatch != null) {
                    val streamUrl = mixdropMatch.groupValues[1]
                    return if (streamUrl.startsWith("//")) "https:$streamUrl" else streamUrl
                }

                // 5. Direct MP4 regex
                val mp4Match = Regex("""["'](https?://[^"']+\.mp4[^"']*)["']""", RegexOption.IGNORE_CASE).find(html)
                if (mp4Match != null) {
                    return mp4Match.groupValues[1]
                }
            }
        } catch (_: Exception) { }

        return embedUrl
    }

    private fun resolveTmdbId(title: String, tmdbApiKey: String?): String {
        if (!tmdbApiKey.isNullOrBlank()) {
            try {
                val encodedQuery = URLEncoder.encode(title, "UTF-8")
                val url = "https://api.themoviedb.org/3/search/movie?api_key=$tmdbApiKey&query=$encodedQuery&language=tr-TR"
                val request = Request.Builder().url(url).header("User-Agent", getRandomUserAgent()).build()
                client.newCall(request).execute().use { response ->
                    if (response.isSuccessful) {
                        val body = response.body?.string().orEmpty()
                        val jsonObj = gson.fromJson(body, JsonObject::class.java)
                        val results = jsonObj.getAsJsonArray("results")
                        if (results != null && results.size() > 0) {
                            val firstMovie = results.get(0).asJsonObject
                            val id = firstMovie.get("id")?.asString
                            if (!id.isNullOrBlank()) {
                                return id
                            }
                        }
                    }
                }
            } catch (_: Exception) { }
        }

        val hash = kotlin.math.abs(title.lowercase().hashCode()) % 800000 + 100000
        return hash.toString()
    }
}
