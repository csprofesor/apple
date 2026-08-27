package com.csprofesor

import com.lagradost.cloudstream3.*
import com.lagradost.cloudstream3.utils.*
import org.jsoup.nodes.Element

class HintFilmIzle : MainAPI() {
    override var mainUrl = "https://www.hintfilmizle.com"
    override var name = "HintFilmİzle"
    override val lang = "tr"
    override val supportedTypes = setOf(TvType.Movie, TvType.TvSeries)
    override val hasMainPage = true
    override val mainPage = mainPageOf(
        "$mainUrl/film-izle" to "Filmler",
        "$mainUrl/yabanci-diziler" to "Yabancı Diziler",
        "$mainUrl/yeni-filmler" to "Yeni Filmler",
        "$mainUrl/en-iyiler" to "En İyiler"
    )

    override suspend fun getMainPage(page: Int, request: MainPageRequest): HomePageResponse {
        val url = request.data + if (page > 1) "/page/" + page else ""
        val items = app.get(url).document.select("a[href]")
            .mapNotNull { it.toSearchResponse() }.distinctBy { it.url }
        return newHomePageResponse(request.name, items, hasNext = items.isNotEmpty())
    }

    override suspend fun search(query: String): List<SearchResponse> =
        app.get(mainUrl + "/?s=" + query.urlEncode()).document.select("a[href]")
            .mapNotNull { it.toSearchResponse() }.distinctBy { it.url }

    private fun Element.toSearchResponse(): SearchResponse? {
        val href = absUrl("href").takeIf { it.startsWith(mainUrl) } ?: return null
        if (href == mainUrl || href == "$mainUrl/" || href.contains("/kategori/") ||
            href.contains("/tur/") || href.contains("/koleksiyon/") ||
            href.contains("/forum") || href.contains("/iletisim")) return null
        val title = selectFirst("img")?.attr("alt")?.trim()?.removeSuffix(" izle")
            ?.takeIf { it.isNotBlank() } ?: text().trim().takeIf { it.isNotBlank() } ?: return null
        val poster = selectFirst("img")?.let { it.absUrl("data-src").ifBlank { it.absUrl("src") } }
        val year = Regex("\b(19|20)\d{2}\b").find(parent()?.text().orEmpty())?.value?.toIntOrNull()
        return newMovieSearchResponse(title, href, TvType.Movie, poster, year)
    }

    override suspend fun load(url: String): LoadResponse {
        val document = app.get(url).document
        val title = document.selectFirst("h1")?.text()?.trim()
            ?: document.selectFirst("meta[property=og:title]")?.attr("content")?.trim()
            ?: url.substringAfterLast("/").replace("-", " ")
        val poster = document.selectFirst("meta[property=og:image]")?.attr("content")
        val plot = document.selectFirst("meta[name=description]")?.attr("content")
            ?: document.selectFirst(".entry-content, .film-description, .description")?.text()?.trim()
        val episodes = document.select("a[href]").mapNotNull { a ->
            val href = a.absUrl("href")
            val text = a.text().trim()
            val m = Regex("(\d+)\.?\s*Bölüm.*?(\d+)\.?\s*Sezon", RegexOption.IGNORE_CASE).find(text)
            if (m != null && href.startsWith(mainUrl)) Episode(
                name = text, season = m.groupValues[2].toIntOrNull() ?: 1,
                episode = m.groupValues[1].toIntOrNull() ?: 1, data = href
            ) else null
        }
        return if (episodes.isNotEmpty()) newTvSeriesLoadResponse(title, url, TvType.TvSeries, episodes) {
            this.posterUrl = poster; this.plot = plot
        } else newMovieLoadResponse(title, url, TvType.Movie, url) {
            this.posterUrl = poster; this.plot = plot
        }
    }

    override suspend fun loadLinks(
        data: String, isCasting: Boolean,
        subtitleCallback: (SubtitleFile) -> Unit,
        callback: (ExtractorLink) -> Unit
    ): Boolean {
        val document = app.get(data).document
        document.select("iframe[src], video[src], video source[src]").forEach {
            val src = it.absUrl("src").takeIf { s -> s.isNotBlank() } ?: return@forEach
            loadExtractor(src, data, subtitleCallback, callback)
        }
        document.select("a[href]").forEach {
            val href = it.absUrl("href")
            if (href.contains("vidmoly", true) || href.contains("vidhide", true) ||
                href.contains("streamtape", true) || href.contains("voe.sx", true) || href.contains("ok.ru", true))
                loadExtractor(href, data, subtitleCallback, callback)
        }
        return true
    }
}
