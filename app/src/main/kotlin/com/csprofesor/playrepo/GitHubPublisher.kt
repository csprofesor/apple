package com.csprofesor.playrepo

import android.util.Base64
import com.google.gson.Gson
import com.google.gson.JsonArray
import com.google.gson.JsonObject
import com.google.gson.reflect.TypeToken
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

class GitHubPublisher {

    companion object {
        const val DEFAULT_OWNER = "csprofesor"
        const val DEFAULT_REPO = "apple"
        const val DEFAULT_BRANCH = "main"
        const val LOGO_URL = "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png"
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(20, TimeUnit.SECONDS)
        .build()

    private val gson = Gson()

    fun cleanPosterUrl(posterUrl: String?, imdbId: String? = null, fallback: String = LOGO_URL): String {
        var res = ""
        if (!posterUrl.isNullOrBlank() && !posterUrl.contains("metahub.space") && posterUrl.startsWith("http")) {
            res = posterUrl
        } else if (!imdbId.isNullOrBlank() && imdbId.startsWith("tt")) {
            try {
                val req = Request.Builder()
                    .url("https://v3.sg.media-imdb.com/suggestion/t/$imdbId.json")
                    .header("User-Agent", "Mozilla/5.0")
                    .build()
                client.newCall(req).execute().use { resp ->
                    if (resp.isSuccessful) {
                        val body = resp.body?.string().orEmpty()
                        val jsonObj = gson.fromJson(body, JsonObject::class.java)
                        val dArr = jsonObj.getAsJsonArray("d")
                        if (dArr != null && dArr.size() > 0) {
                            val imgUrl = dArr.get(0).asJsonObject.getAsJsonObject("i")?.get("imageUrl")?.asString
                            if (!imgUrl.isNullOrBlank() && imgUrl.startsWith("http")) {
                                res = imgUrl
                            }
                        }
                    }
                }
            } catch (_: Exception) {}
        }

        if (res.isBlank() && fallback.isNotBlank() && !fallback.contains("metahub.space") && fallback.startsWith("http")) {
            res = fallback
        }
        if (res.isBlank()) {
            res = LOGO_URL
        }

        if (res.contains("@")) {
            res = res.replace("@", "%40")
        }
        return res
    }

    suspend fun publishBatchToGitHub(
        githubToken: String = BuildConfig.GITHUB_TOKEN,
        repoOwner: String = DEFAULT_OWNER,
        repoName: String = DEFAULT_REPO,
        scrapedMovies: List<ScrapedMovie>
    ): Result<String> = withContext(Dispatchers.IO) {
        val effectiveToken = githubToken.ifBlank { BuildConfig.GITHUB_TOKEN }

        if (effectiveToken.isBlank()) {
            return@withContext Result.failure(Exception("GitHub Token bulunamadı. Lütfen ayarları kontrol edin."))
        }

        if (scrapedMovies.isEmpty()) {
            return@withContext Result.success("Yüklenecek film bulunamadı.")
        }

        try {
            val filesToCommit = mutableMapOf<String, String>()

            // 1. Process & merge data/streams.json
            val streamsJson = prepareMergedStreamsJson(
                githubToken = effectiveToken,
                repoOwner = repoOwner,
                repoName = repoName,
                scrapedMovies = scrapedMovies
            )
            filesToCommit["data/streams.json"] = streamsJson

            // 2. Process & merge data/catalog.json
            val (catalogJson, updatedMetas) = prepareMergedCatalogJson(
                githubToken = effectiveToken,
                repoOwner = repoOwner,
                repoName = repoName,
                scrapedMovies = scrapedMovies
            )
            filesToCommit["data/catalog.json"] = catalogJson

            // 3. Prepare category catalogs (playrepo/catalog/movie/*.json)
            val categoryFiles = prepareCategoryCatalogs(updatedMetas)
            categoryFiles.forEach { (path, content) ->
                filesToCommit[path] = content
            }

            // 4. Prepare static detail meta and stream files for EVERY scraped movie
            scrapedMovies.forEach { movie ->
                val idStr = normalizeMovieId(movie.tmdbId)
                val hyphenId = idStr.replace(":", "-").replace("_", "-")
                val safeId = idStr.replace(":", "_")
                val cleanId = idStr.removePrefix("playrepo-").removePrefix("tmdb-").removePrefix("movie/").removePrefix("tv/")

                val metaJson = buildMetaJson(movie, idStr)
                val streamJson = buildStreamJson(movie)

                val distinctIds = linkedSetOf(idStr, hyphenId, safeId, cleanId).filter { it.isNotBlank() }
                for (id in distinctIds) {
                    filesToCommit["playrepo/meta/movie/$id.json"] = metaJson
                    filesToCommit["playrepo/stream/movie/$id.json"] = streamJson
                    if (movie.type == "series") {
                        filesToCommit["playrepo/meta/series/$id.json"] = metaJson
                        if (movie.season != null && movie.episode != null) {
                            val epId = "$id:${movie.season}:${movie.episode}"
                            val safeEpId = epId.replace(":", "-")
                            filesToCommit["playrepo/stream/series/$epId.json"] = streamJson
                            filesToCommit["playrepo/stream/series/$safeEpId.json"] = streamJson
                        }
                    }
                }
            }

            // 5. Commit all files atomically in ONE commit via Git Data API
            val atomicSuccess = commitBatchUsingGitDataApi(
                githubToken = effectiveToken,
                repoOwner = repoOwner,
                repoName = repoName,
                branch = DEFAULT_BRANCH,
                commitMessage = "PlayRepo: Sync ${scrapedMovies.size} filmleri, kategorileri ve detay sayfalarını güncelle",
                files = filesToCommit
            )

            if (atomicSuccess) {
                Result.success("Toplam ${scrapedMovies.size} film, kategoriler ve detay sayfaları eksiksiz yüklendi!")
            } else {
                // Fallback: upload core files sequentially if Git Data API fails
                fallbackUploadFiles(effectiveToken, repoOwner, repoName, filesToCommit)
                Result.success("Toplam ${scrapedMovies.size} film yüklendi (yedek aktarım modu).")
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    private fun normalizeMovieId(rawId: String): String {
        return when {
            rawId.startsWith("tt") -> rawId
            rawId.startsWith("tmdb-") || rawId.startsWith("tmdb:") -> "tmdb-" + rawId.substringAfter(":")
            rawId.startsWith("playrepo-") || rawId.startsWith("playrepo:") -> "playrepo-" + rawId.substringAfter(":")
            else -> "playrepo-$rawId"
        }
    }

    private fun prepareMergedStreamsJson(
        githubToken: String,
        repoOwner: String,
        repoName: String,
        scrapedMovies: List<ScrapedMovie>
    ): String {
        val apiUrl = "https://api.github.com/repos/$repoOwner/$repoName/contents/data/streams.json"
        var currentDataMap = mutableMapOf<String, List<StreamItem>>()

        try {
            val getRequest = Request.Builder()
                .url(apiUrl)
                .header("Authorization", "Bearer $githubToken")
                .header("Accept", "application/vnd.github.v3+json")
                .build()

            client.newCall(getRequest).execute().use { response ->
                if (response.isSuccessful) {
                    val bodyString = response.body?.string().orEmpty()
                    val jsonObj = gson.fromJson(bodyString, JsonObject::class.java)
                    val base64Content = jsonObj.get("content")?.asString?.replace("\n", "")?.replace("\r", "").orEmpty()
                    if (base64Content.isNotBlank()) {
                        val decodedBytes = Base64.decode(base64Content, Base64.DEFAULT)
                        val decodedJson = String(decodedBytes, Charsets.UTF_8)
                        val typeToken = object : TypeToken<MutableMap<String, List<StreamItem>>>() {}.type
                        val parsedMap: MutableMap<String, List<StreamItem>>? = gson.fromJson(decodedJson, typeToken)
                        if (parsedMap != null) {
                            currentDataMap = parsedMap
                        }
                    }
                }
            }
        } catch (_: Exception) {}

        val cleanedDataMap = mutableMapOf<String, List<StreamItem>>()
        currentDataMap.forEach { (key, streams) ->
            val valid = streams.filter { st ->
                val u = st.url.lowercase()
                u != "about:blank" && !u.startsWith("about:") &&
                (u.contains(".m3u8") || u.contains(".mp4") || u.contains("/manifest") || u.contains("/video"))
            }
            if (valid.isNotEmpty()) {
                cleanedDataMap[key] = valid
            }
        }

        scrapedMovies.forEach { movie ->
            val idStr = normalizeMovieId(movie.tmdbId)
            val hyphenId = idStr.replace(":", "-").replace("_", "-")
            val safeId = idStr.replace(":", "_")
            val cleanId = idStr.replace("playrepo-", "").replace("playrepo:", "").replace("tmdb-", "").replace("tmdb:", "")

            val existingStreams = (cleanedDataMap[idStr] ?: cleanedDataMap[hyphenId] ?: cleanedDataMap[safeId] ?: cleanedDataMap[cleanId])?.toMutableList() ?: mutableListOf()

            movie.streams.forEach { newStream ->
                val u = newStream.url.lowercase()
                if (u.contains(".m3u8") || u.contains(".mp4") || u.contains("/manifest")) {
                    if (existingStreams.none { it.url == newStream.url }) {
                        existingStreams.add(newStream)
                    }
                }
            }

            if (existingStreams.isNotEmpty()) {
                // Ensure HLS (.m3u8) is always FIRST for zero-wait instant start
                val sorted = existingStreams.sortedWith(
                    compareByDescending<StreamItem> { it.format == "m3u8" || it.url.contains(".m3u8") }
                        .thenByDescending { it.quality }
                )

                val dubCount = java.util.concurrent.atomic.AtomicInteger(0)
                val subCount = java.util.concurrent.atomic.AtomicInteger(0)
                val otherCount = java.util.concurrent.atomic.AtomicInteger(0)

                val renumbered = sorted.map { st ->
                    val isDub = st.title.contains("Dublaj")
                    val isSub = st.title.contains("Altyaz")
                    val label = when {
                        isDub -> "Dublaj ${dubCount.incrementAndGet()}"
                        isSub -> "Altyazı ${subCount.incrementAndGet()}"
                        else -> "Sunucu ${otherCount.incrementAndGet()}"
                    }
                    val parts = st.title.split("•")
                    val suffix = if (parts.size > 2) parts.drop(2).joinToString(" • ").trim() else if (isDub) "1080p • [TR Dublaj]" else "1080p • [TR Altyazılı]"
                    val ref = st.referer?.ifBlank { st.url } ?: st.url
                    val isM3u8 = st.format == "m3u8" || st.url.contains(".m3u8")
                    st.copy(
                        name = "Play",
                        title = "Play • $label • $suffix",
                        format = if (isM3u8) "m3u8" else "mp4",
                        headers = mapOf(
                            "User-Agent" to "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                            "Referer" to ref
                        )
                    )
                }

                cleanedDataMap[idStr] = renumbered
                cleanedDataMap[hyphenId] = renumbered
                cleanedDataMap[safeId] = renumbered
                cleanedDataMap[cleanId] = renumbered
                cleanedDataMap["movie/$cleanId"] = renumbered
            }
        }

        return gson.toJson(cleanedDataMap)
    }

    private fun prepareMergedCatalogJson(
        githubToken: String,
        repoOwner: String,
        repoName: String,
        scrapedMovies: List<ScrapedMovie>
    ): Pair<String, List<JsonObject>> {
        var currentCatalogMetas = mutableListOf<JsonObject>()
        val apiUrl = "https://api.github.com/repos/$repoOwner/$repoName/contents/data/catalog.json"

        try {
            val getRequest = Request.Builder()
                .url(apiUrl)
                .header("Authorization", "Bearer $githubToken")
                .header("Accept", "application/vnd.github.v3+json")
                .build()

            client.newCall(getRequest).execute().use { response ->
                if (response.isSuccessful) {
                    val bodyString = response.body?.string().orEmpty()
                    val jsonObj = gson.fromJson(bodyString, JsonObject::class.java)
                    val base64Content = jsonObj.get("content")?.asString?.replace("\n", "")?.replace("\r", "").orEmpty()
                    if (base64Content.isNotBlank()) {
                        val decodedBytes = Base64.decode(base64Content, Base64.DEFAULT)
                        val decodedJson = String(decodedBytes, Charsets.UTF_8)
                        val catalogObj = gson.fromJson(decodedJson, JsonObject::class.java)
                        if (catalogObj != null && catalogObj.has("metas")) {
                            val typeToken = object : TypeToken<MutableList<JsonObject>>() {}.type
                            val parsedMetas: MutableList<JsonObject>? = gson.fromJson(catalogObj.getAsJsonArray("metas"), typeToken)
                            if (parsedMetas != null) {
                                currentCatalogMetas = parsedMetas
                            }
                        }
                    }
                }
            }
        } catch (_: Exception) {}

        scrapedMovies.forEach { movie ->
            val idStr = normalizeMovieId(movie.tmdbId)
            val hyphenId = idStr.replace(":", "-").replace("_", "-")
            val cleanId = idStr.replace("playrepo-", "").replace("playrepo:", "").replace("tmdb-", "").replace("tmdb:", "")

            val genreJsonArray = JsonArray()
            movie.genres.forEach { genreJsonArray.add(it) }

            val cleanPoster = cleanPosterUrl(movie.poster, idStr, LOGO_URL)
            val catObj = JsonObject().apply {
                addProperty("id", idStr)
                addProperty("type", movie.type)
                addProperty("name", movie.title)
                addProperty("poster", cleanPoster)
                addProperty("background", cleanPoster)
                addProperty("logo", LOGO_URL)
                addProperty("description", if (!movie.description.isNullOrBlank()) movie.description else "Play • ${movie.streams.size} Sunucu Seçeneği • 1080p TR")
                if (!movie.year.isNullOrBlank()) {
                    addProperty("releaseInfo", movie.year)
                }
                if (!movie.imdbRating.isNullOrBlank()) {
                    addProperty("imdbRating", movie.imdbRating)
                }
                if (!movie.runtime.isNullOrBlank()) {
                    addProperty("runtime", movie.runtime)
                }
                add("genres", genreJsonArray)
            }

            val existingIndex = currentCatalogMetas.indexOfFirst {
                val itId = it.get("id")?.asString
                itId == idStr || itId == hyphenId || itId == cleanId || itId == "movie/$cleanId" || itId == "tmdb:$cleanId" || itId == "playrepo:$cleanId"
            }

            if (existingIndex >= 0) {
                currentCatalogMetas[existingIndex] = catObj
            } else {
                currentCatalogMetas.add(0, catObj)
            }
        }

        currentCatalogMetas.forEach { item ->
            val p = item.get("poster")?.asString.orEmpty()
            val id = item.get("id")?.asString.orEmpty()
            val clean = cleanPosterUrl(p, id, LOGO_URL)
            item.addProperty("poster", clean)
            item.addProperty("background", clean)
        }

        val finalWrapper = JsonObject().apply {
            val metasArray = JsonArray()
            currentCatalogMetas.forEach { metasArray.add(it) }
            add("metas", metasArray)
        }

        return Pair(gson.toJson(finalWrapper), currentCatalogMetas)
    }

    private fun prepareCategoryCatalogs(allMetas: List<JsonObject>): Map<String, String> {
        val categoryConfigs = listOf(
            // Film Katalogları
            "playrepo/catalog/movie/playrepo-popular.json" to { _: JsonObject -> true },
            "playrepo/catalog/movie/playrepo-catalog-movies.json" to { m: JsonObject -> m.get("type")?.asString != "series" },
            "playrepo/catalog/movie/playrepo-aksiyon.json" to { m: JsonObject -> m.get("type")?.asString != "series" && hasGenre(m, "aksiyon", "macera") },
            "playrepo/catalog/movie/playrepo-komedi.json" to { m: JsonObject -> m.get("type")?.asString != "series" && hasGenre(m, "komedi", "mizah") },
            "playrepo/catalog/movie/playrepo-dram.json" to { m: JsonObject -> m.get("type")?.asString != "series" && hasGenre(m, "dram", "romantik") },
            "playrepo/catalog/movie/playrepo-korku.json" to { m: JsonObject -> m.get("type")?.asString != "series" && hasGenre(m, "korku", "gerilim") },
            "playrepo/catalog/movie/playrepo-bilimkurgu.json" to { m: JsonObject -> m.get("type")?.asString != "series" && hasGenre(m, "bilim", "kurgu", "fantastik") },
            "playrepo/catalog/movie/playrepo-animasyon.json" to { m: JsonObject -> m.get("type")?.asString != "series" && hasGenre(m, "animasyon", "çizgi", "anime", "animation") },
            "playrepo/catalog/movie/playrepo-belgesel.json" to { m: JsonObject -> m.get("type")?.asString != "series" && hasGenre(m, "belgesel", "tarih", "biyografi", "documentary", "history") },
            "playrepo/catalog/movie/playrepo-suc.json" to { m: JsonObject -> m.get("type")?.asString != "series" && hasGenre(m, "suç", "suc", "polisiye", "gizem", "crime", "mystery") },
            // Dizi Katalogları
            "playrepo/catalog/series/playrepo-series-popular.json" to { m: JsonObject -> m.get("type")?.asString == "series" },
            "playrepo/catalog/series/playrepo-series-dram.json" to { m: JsonObject -> m.get("type")?.asString == "series" && hasGenre(m, "dram", "romantik") },
            "playrepo/catalog/series/playrepo-series-aksiyon.json" to { m: JsonObject -> m.get("type")?.asString == "series" && hasGenre(m, "aksiyon", "macera") },
            "playrepo/catalog/series/playrepo-series-komedi.json" to { m: JsonObject -> m.get("type")?.asString == "series" && hasGenre(m, "komedi", "mizah") },
            "playrepo/catalog/series/playrepo-series-suc.json" to { m: JsonObject -> m.get("type")?.asString == "series" && hasGenre(m, "suç", "suc", "polisiye", "gizem", "crime", "mystery") },
            "playrepo/catalog/series/playrepo-series-bilimkurgu.json" to { m: JsonObject -> m.get("type")?.asString == "series" && hasGenre(m, "bilim", "kurgu", "fantastik") }
        )

        val result = mutableMapOf<String, String>()
        categoryConfigs.forEach { (filePath, predicate) ->
            val filtered = allMetas.filter { predicate(it) }
            val targetMetas = if (filePath.contains("popular") || filePath.contains("catalog-movies")) {
                filtered.take(60)
            } else {
                filtered
            }

            val finalWrapper = JsonObject().apply {
                val metasArray = JsonArray()
                targetMetas.forEach { metasArray.add(it) }
                add("metas", metasArray)
            }
            result[filePath] = gson.toJson(finalWrapper)
        }

        return result
    }

    private fun buildMetaJson(movie: ScrapedMovie, idStr: String): String {
        val cleanPoster = cleanPosterUrl(movie.poster, idStr, LOGO_URL)
        val metaWrapper = JsonObject().apply {
            val m = JsonObject().apply {
                addProperty("id", idStr)
                addProperty("type", movie.type)
                addProperty("name", movie.title)
                addProperty("poster", cleanPoster)
                addProperty("background", cleanPoster)
                addProperty("logo", LOGO_URL)
                addProperty("description", if (!movie.description.isNullOrBlank()) movie.description else "Play • ${movie.streams.size} Sunucu Seçeneği • 1080p TR")
                if (!movie.year.isNullOrBlank()) {
                    addProperty("releaseInfo", movie.year)
                }
                if (!movie.imdbRating.isNullOrBlank()) {
                    addProperty("imdbRating", movie.imdbRating)
                }
                if (!movie.runtime.isNullOrBlank()) {
                    addProperty("runtime", movie.runtime)
                }
                if (!movie.director.isNullOrBlank()) {
                    val dirArr = JsonArray().apply { add(movie.director) }
                    add("director", dirArr)
                }
                if (!movie.cast.isNullOrEmpty()) {
                    val castArr = JsonArray()
                    movie.cast.forEach { castArr.add(it) }
                    add("cast", castArr)
                }
                val genreArr = JsonArray()
                movie.genres.forEach { genreArr.add(it) }
                add("genres", genreArr)

                val defaultVid = if (movie.type == "series" && movie.season != null && movie.episode != null) {
                    "$idStr:${movie.season}:${movie.episode}"
                } else idStr

                val bh = JsonObject().apply {
                    addProperty("defaultVideoId", defaultVid)
                    addProperty("hasScheduledVideos", false)
                }
                add("behaviorHints", bh)

                val videos = JsonArray().apply {
                    val v = JsonObject().apply {
                        addProperty("id", defaultVid)
                        addProperty("title", movie.title)
                        if (movie.type == "series") {
                            if (movie.season != null) addProperty("season", movie.season)
                            if (movie.episode != null) addProperty("episode", movie.episode)
                        }
                        if (!movie.year.isNullOrBlank()) {
                            addProperty("released", "${movie.year}-01-01T00:00:00.000Z")
                        }
                    }
                    add(v)
                }
                add("videos", videos)
            }
            add("meta", m)
        }
        return gson.toJson(metaWrapper)
    }

    private fun buildStreamJson(movie: ScrapedMovie): String {
        val streamWrapper = JsonObject().apply {
            val sArr = JsonArray()
            movie.streams.forEachIndexed { idx, st ->
                val ref = st.referer?.ifBlank { st.url } ?: st.url
                val isM3u8 = st.format == "m3u8" || st.url.contains(".m3u8")
                val cleanTitle = if (st.title.isNotBlank()) st.title else "Play • Sunucu ${idx + 1}"

                val so = JsonObject().apply {
                    addProperty("name", "Play")
                    addProperty("title", cleanTitle)
                    addProperty("url", st.url)
                    addProperty("quality", st.quality)
                    addProperty("format", if (isM3u8) "m3u8" else "mp4")

                    val headersObj = JsonObject().apply {
                        addProperty("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
                        addProperty("Referer", ref)
                    }
                    add("headers", headersObj)

                    val bh = JsonObject().apply {
                        addProperty("notSupported", false)
                        val proxyHeaders = JsonObject().apply {
                            val reqObj = JsonObject().apply {
                                addProperty("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
                                addProperty("Referer", ref)
                            }
                            add("request", reqObj)
                        }
                        add("proxyHeaders", proxyHeaders)
                    }
                    add("behaviorHints", bh)

                    if (!st.subtitles.isNullOrEmpty()) {
                        val subArr = JsonArray()
                        st.subtitles.forEach { sub ->
                            val subObj = JsonObject().apply {
                                addProperty("id", sub.id)
                                addProperty("url", sub.url)
                                addProperty("lang", sub.lang)
                            }
                            subArr.add(subObj)
                        }
                        add("subtitles", subArr)
                    }
                }
                sArr.add(so)
            }
            add("streams", sArr)
        }
        return gson.toJson(streamWrapper)
    }

    private fun commitBatchUsingGitDataApi(
        githubToken: String,
        repoOwner: String,
        repoName: String,
        branch: String = DEFAULT_BRANCH,
        commitMessage: String,
        files: Map<String, String>
    ): Boolean {
        try {
            val authHeader = if (githubToken.startsWith("ghp_") || githubToken.startsWith("github_pat_")) "token $githubToken" else "Bearer $githubToken"

            // 1. Get latest commit SHA on refs/heads/branch
            val refUrl = "https://api.github.com/repos/$repoOwner/$repoName/git/ref/heads/$branch"
            val refReq = Request.Builder()
                .url(refUrl)
                .header("Authorization", authHeader)
                .header("Accept", "application/vnd.github.v3+json")
                .build()

            val parentSha = client.newCall(refReq).execute().use { resp ->
                if (!resp.isSuccessful) return false
                val j = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                j.getAsJsonObject("object")?.get("sha")?.asString
            } ?: return false

            // 2. Get base tree SHA from the latest commit (REQUIRED by Git Trees API)
            val commitGetUrl = "https://api.github.com/repos/$repoOwner/$repoName/git/commits/$parentSha"
            val commitGetReq = Request.Builder()
                .url(commitGetUrl)
                .header("Authorization", authHeader)
                .header("Accept", "application/vnd.github.v3+json")
                .build()

            val baseTreeSha = client.newCall(commitGetReq).execute().use { resp ->
                if (!resp.isSuccessful) return false
                val j = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                j.getAsJsonObject("tree")?.get("sha")?.asString
            } ?: return false

            // 3. Build Git tree with inline content (GitHub automatically creates blobs atomically in 1 request)
            val treeArray = JsonArray()
            files.forEach { (path, content) ->
                val item = JsonObject().apply {
                    addProperty("path", path)
                    addProperty("mode", "100644")
                    addProperty("type", "blob")
                    addProperty("content", content)
                }
                treeArray.add(item)
            }

            if (treeArray.size() == 0) return false

            val treeBody = JsonObject().apply {
                addProperty("base_tree", baseTreeSha)
                add("tree", treeArray)
            }

            val treeReq = Request.Builder()
                .url("https://api.github.com/repos/$repoOwner/$repoName/git/trees")
                .header("Authorization", authHeader)
                .header("Accept", "application/vnd.github.v3+json")
                .post(treeBody.toString().toRequestBody("application/json".toMediaType()))
                .build()

            val newTreeSha = client.newCall(treeReq).execute().use { resp ->
                if (!resp.isSuccessful) return false
                val j = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                j.get("sha")?.asString
            } ?: return false

            // 4. Post new commit referencing newTreeSha
            val commitUrl = "https://api.github.com/repos/$repoOwner/$repoName/git/commits"
            val commitBody = JsonObject().apply {
                addProperty("message", commitMessage)
                addProperty("tree", newTreeSha)
                val parentsArr = JsonArray().apply { add(parentSha) }
                add("parents", parentsArr)
            }

            val commitReq = Request.Builder()
                .url(commitUrl)
                .header("Authorization", authHeader)
                .header("Accept", "application/vnd.github.v3+json")
                .post(commitBody.toString().toRequestBody("application/json".toMediaType()))
                .build()

            val newCommitSha = client.newCall(commitReq).execute().use { resp ->
                if (!resp.isSuccessful) return false
                val j = gson.fromJson(resp.body?.string(), JsonObject::class.java)
                j.get("sha")?.asString
            } ?: return false

            // 5. Update branch ref to the new commit
            val updateRefBody = JsonObject().apply {
                addProperty("sha", newCommitSha)
                addProperty("force", true)
            }

            val updateReq = Request.Builder()
                .url(refUrl)
                .header("Authorization", authHeader)
                .header("Accept", "application/vnd.github.v3+json")
                .patch(updateRefBody.toString().toRequestBody("application/json".toMediaType()))
                .build()

            return client.newCall(updateReq).execute().use { resp ->
                resp.isSuccessful
            }
        } catch (_: Exception) {
            return false
        }
    }

    private fun fallbackUploadFiles(
        githubToken: String,
        repoOwner: String,
        repoName: String,
        files: Map<String, String>
    ) {
        files.forEach { (filePath, content) ->
            try {
                val apiUrl = "https://api.github.com/repos/$repoOwner/$repoName/contents/$filePath"
                val getRequest = Request.Builder()
                    .url(apiUrl)
                    .header("Authorization", "Bearer $githubToken")
                    .header("Accept", "application/vnd.github.v3+json")
                    .build()

                var sha: String? = null
                client.newCall(getRequest).execute().use { response ->
                    if (response.isSuccessful) {
                        val bodyString = response.body?.string().orEmpty()
                        val jsonObj = gson.fromJson(bodyString, JsonObject::class.java)
                        sha = jsonObj.get("sha")?.asString
                    }
                }

                val encodedContent = Base64.encodeToString(content.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
                val putBody = JsonObject().apply {
                    addProperty("message", "Sync $filePath")
                    addProperty("content", encodedContent)
                    if (sha != null) {
                        addProperty("sha", sha)
                    }
                }

                val putRequest = Request.Builder()
                    .url(apiUrl)
                    .header("Authorization", "Bearer $githubToken")
                    .header("Accept", "application/vnd.github.v3+json")
                    .put(putBody.toString().toRequestBody("application/json".toMediaType()))
                    .build()

                client.newCall(putRequest).execute().close()
            } catch (_: Exception) {}
        }
    }

    private fun hasGenre(movieObj: JsonObject, vararg keywords: String): Boolean {
        val genresArr = movieObj.getAsJsonArray("genres") ?: return false
        for (g in genresArr) {
            val text = g.asString.lowercase()
            for (kw in keywords) {
                if (text.contains(kw)) return true
            }
        }
        return false
    }

    suspend fun publishToGitHub(
        githubToken: String = BuildConfig.GITHUB_TOKEN,
        repoOwner: String = DEFAULT_OWNER,
        repoName: String = DEFAULT_REPO,
        scrapedMovie: ScrapedMovie
    ): Result<String> = publishBatchToGitHub(githubToken, repoOwner, repoName, listOf(scrapedMovie))
}
