package com.csprofesor.playrepo

import com.google.gson.Gson
import com.google.gson.JsonObject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import org.jsoup.Jsoup
import java.net.URLEncoder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import kotlin.random.Random

class SiteScraperEngine {

    private val cookieStore = ConcurrentHashMap<String, List<Cookie>>()

    private val cookieJar = object : CookieJar {
        override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
            cookieStore[url.host] = cookies
        }

        override fun loadForRequest(url: HttpUrl): List<Cookie> {
            return cookieStore[url.host] ?: emptyList()
        }
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .followRedirects(true)
        .followSslRedirects(true)
        .cookieJar(cookieJar)
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
                    .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8")
                    .header("Accept-Language", "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7")
                    .header("Sec-Ch-Ua", "\"Chromium\";v=\"124\", \"Google Chrome\";v=\"124\"")
                    .header("Sec-Ch-Ua-Mobile", "?0")
                    .header("Sec-Ch-Ua-Platform", "\"Windows\"")
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
            lower.endsWith(".png") || lower.endsWith(".css") || lower.endsWith(".js") ||
            lower.contains("/login") || lower.contains("/takvim") || lower.contains("/trendler") ||
            lower.contains("/seri-filmler") || lower.contains("/yabanci-dizi")
        ) {
            return false
        }

        return lower.contains("/film/") || lower.contains("-izle") || lower.contains("/dizi/")
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
                if (movie != null && movie.streams.isNotEmpty()) {
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
            .header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
            .header("Referer", movieUrl)
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) return@withContext null

            val html = response.body?.string().orEmpty()
            val doc = Jsoup.parse(html, movieUrl)

            val rawTitle = doc.selectFirst("h1")?.text()?.trim()
                ?: doc.selectFirst("meta[property=og:title]")?.attr("content")?.trim()
                ?: movieUrl.substringAfterLast("/").replace("-", " ")

            val cleanTitle = cleanMovieTitle(rawTitle)
            if (cleanTitle.isBlank() || cleanTitle.lowercase() == "filmkovasi.co" || cleanTitle.lowercase() == "www.hintfilmizle.com") return@withContext null

            // Detect language & quality flags
            val isDub = html.contains("dublaj", true) || rawTitle.contains("dublaj", true)
            val langTag = if (isDub) "[TR Dublaj]" else "[TR Altyazılı]"
            val qualityTag = if (html.contains("4k", true) || rawTitle.contains("4k", true)) "4K" else "1080p"

            // Extract genres / category classification
            val genreList = mutableListOf<String>()
            val lowerHtml = html.lowercase()
            if (lowerHtml.contains("korku") || lowerHtml.contains("gerilim")) genreList.add("Korku & Gerilim")
            if (lowerHtml.contains("aksiyon") || lowerHtml.contains("macera")) genreList.add("Aksiyon & Macera")
            if (lowerHtml.contains("komedi") || lowerHtml.contains("mizah")) genreList.add("Komedi")
            if (lowerHtml.contains("dram") || lowerHtml.contains("romantik")) genreList.add("Dram & Romantik")
            if (lowerHtml.contains("bilim") || lowerHtml.contains("kurgu") || lowerHtml.contains("fantastik")) genreList.add("Bilim Kurgu & Fantastik")
            if (genreList.isEmpty()) genreList.add("Popüler")

            val foundCandidateUrls = mutableListOf<String>()

            // 1. Scrape direct iframe & video tags
            doc.select("iframe[src], video[src], video source[src]").forEach { el ->
                val src = el.absUrl("src").ifBlank { el.attr("src") }
                if (src.isNotBlank() && !src.contains("about:blank")) {
                    foundCandidateUrls.add(src)
                }
            }

            // 2. Scrape data-src / data-url / data-player / data-embed attributes
            doc.select("[data-src], [data-url], [data-player], [data-embed]").forEach { el ->
                val dataSrc = el.attr("data-src").ifBlank { el.attr("data-url") }.ifBlank { el.attr("data-player") }.ifBlank { el.attr("data-embed") }
                if (dataSrc.isNotBlank() && !dataSrc.contains("about:blank")) {
                    val fullUrl = if (dataSrc.startsWith("http")) dataSrc else if (dataSrc.startsWith("//")) "https:$dataSrc" else movieUrl
                    foundCandidateUrls.add(fullUrl)
                }
            }

            // 3. Scrape external video links (vidmoly, vidhide, voe, streamtape, ok.ru, kinescope, mixdrop, dood)
            doc.select("a[href]").forEach { a ->
                val href = a.absUrl("href").ifBlank { a.attr("href") }
                if (href.contains("vidmoly") || href.contains("vidhide") || href.contains("filelions") ||
                    href.contains("streamwish") || href.contains("voe.sx") || href.contains("streamtape") ||
                    href.contains("ok.ru") || href.contains("kinescope") || href.contains("odnoklassniki") ||
                    href.contains("mixdrop") || href.contains("dood") || href.contains("vidsrc") || href.contains("vixsrc")
                ) {
                    foundCandidateUrls.add(href)
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
                        foundCandidateUrls.add(urlMatch.value)
                    }
                } catch (_: Exception) { }
            }

            // 5. Scrape direct .m3u8 / .mp4 URLs in raw JS code
            val m3u8Regex = Regex("""https?://[^\s"'`<>]+?\.(?:m3u8|mp4)[^\s"'`<>]*""", RegexOption.IGNORE_CASE)
            m3u8Regex.findAll(html).forEach { match ->
                foundCandidateUrls.add(match.value)
            }

            val resolvedStreams = mutableListOf<StreamItem>()

            foundCandidateUrls.distinct().forEach { embedUrl ->
                val directStreamUrl = unpackEmbedStreamUrl(embedUrl, movieUrl)
                if (directStreamUrl.isNotBlank() && resolvedStreams.none { it.url == directStreamUrl }) {
                    val lowerUrl = directStreamUrl.lowercase()
                    if (lowerUrl.contains(".m3u8") || lowerUrl.contains(".mp4")) {
                        val isM3u8 = lowerUrl.contains(".m3u8")
                        resolvedStreams.add(
                            StreamItem(
                                name = "PlayRepo",
                                title = "PlayRepo • Sunucu ${resolvedStreams.size + 1} • $qualityTag • $langTag",
                                url = directStreamUrl,
                                quality = if (qualityTag == "4K") 2160 else 1080,
                                format = if (isM3u8) "m3u8" else "mp4",
                                referer = embedUrl
                            )
                        )
                    }
                }
            }

            if (resolvedStreams.isEmpty()) {
                return@withContext null
            }

            // Resolve TMDB ID, official Title, and Poster URL
            val (tmdbId, tmdbTitle, posterUrl) = resolveTmdbInfo(cleanTitle, tmdbApiKey)

            ScrapedMovie(
                tmdbId = tmdbId,
                title = tmdbTitle,
                poster = posterUrl,
                streams = resolvedStreams,
                genres = genreList
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

    /**
     * Unpacks/decrypts embed players into direct M3U8 / MP4 URLs.
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

                // 2. VOE regex
                val voeMatch = Regex("""'hls'\s*:\s*['"](https?://[^'"]+)['"]""", RegexOption.IGNORE_CASE).find(html)
                    ?: Regex("""["']hls["']\s*:\s*["'](https?://[^"']+)["']""", RegexOption.IGNORE_CASE).find(html)
                if (voeMatch != null) {
                    return voeMatch.groupValues[1]
                }

                // 3. Ok.ru HLS Manifest
                val okRuMatch = Regex("""hlsManifestUrl["']?\s*:\s*["'](https?://[^"']+)["']""", RegexOption.IGNORE_CASE).find(html)
                if (okRuMatch != null) {
                    return okRuMatch.groupValues[1].replace("\\u0026", "&")
                }

                // 4. Mixdrop regex
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

    private fun resolveTmdbInfo(title: String, tmdbApiKey: String?): Triple<String, String, String> {
        val apiKey = if (tmdbApiKey.isNullOrBlank()) "15d2921542e613f17d23a2a6b29f7f45" else tmdbApiKey
        try {
            val encodedQuery = URLEncoder.encode(title, "UTF-8")
            val url = "https://api.themoviedb.org/3/search/movie?api_key=$apiKey&query=$encodedQuery&language=tr-TR"
            val request = Request.Builder().url(url).header("User-Agent", getRandomUserAgent()).build()
            client.newCall(request).execute().use { response ->
                if (response.isSuccessful) {
                    val body = response.body?.string().orEmpty()
                    val jsonObj = gson.fromJson(body, JsonObject::class.java)
                    val results = jsonObj.getAsJsonArray("results")
                    if (results != null && results.size() > 0) {
                        val firstMovie = results.get(0).asJsonObject
                        val id = firstMovie.get("id")?.asString
                        val officialTitle = firstMovie.get("title")?.asString ?: firstMovie.get("original_title")?.asString ?: title
                        val posterPath = firstMovie.get("poster_path")?.asString
                        val posterUrl = if (!posterPath.isNullOrBlank()) "https://image.tmdb.org/t/p/w500$posterPath" else "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png"

                        if (!id.isNullOrBlank()) {
                            return Triple(id, officialTitle, posterUrl)
                        }
                    }
                }
            }
        } catch (_: Exception) { }

        val hash = kotlin.math.abs(title.lowercase().hashCode()) % 800000 + 100000
        return Triple(hash.toString(), title, "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png")
    }
}
