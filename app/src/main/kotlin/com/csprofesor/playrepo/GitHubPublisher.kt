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

class GitHubPublisher {

    companion object {
        const val DEFAULT_OWNER = "csprofesor"
        const val DEFAULT_REPO = "apple"
    }

    private val client = OkHttpClient()
    private val gson = Gson()

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

        try {
            // 1. Update data/streams.json
            val streamsResult = updateStreamsOnGitHub(
                githubToken = effectiveToken,
                repoOwner = repoOwner,
                repoName = repoName,
                scrapedMovies = scrapedMovies
            )

            // 2. Update data/catalog.json with categories and real posters
            val updatedMetas = updateCatalogOnGitHub(
                githubToken = effectiveToken,
                repoOwner = repoOwner,
                repoName = repoName,
                scrapedMovies = scrapedMovies
            )

            // 3. Update playrepo/catalog/movie/ files for direct Nuvio home screen compatibility
            if (updatedMetas.isNotEmpty()) {
                updateCategoryCatalogsOnGitHub(
                    githubToken = effectiveToken,
                    repoOwner = repoOwner,
                    repoName = repoName,
                    allMetas = updatedMetas
                )
            }

            // 4. Update playrepo/meta/movie/ and playrepo/stream/movie/ for direct Nuvio Play button
            updateMetaAndStreamsOnGitHub(
                githubToken = effectiveToken,
                repoOwner = repoOwner,
                repoName = repoName,
                scrapedMovies = scrapedMovies
            )

            streamsResult
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    private fun updateStreamsOnGitHub(
        githubToken: String,
        repoOwner: String,
        repoName: String,
        scrapedMovies: List<ScrapedMovie>
    ): Result<String> {
        val filePath = "data/streams.json"
        val apiUrl = "https://api.github.com/repos/$repoOwner/$repoName/contents/$filePath"

        val getRequest = Request.Builder()
            .url(apiUrl)
            .header("Authorization", "Bearer $githubToken")
            .header("Accept", "application/vnd.github.v3+json")
            .build()

        var sha: String? = null
        var currentDataMap = mutableMapOf<String, List<StreamItem>>()

        client.newCall(getRequest).execute().use { response ->
            if (response.isSuccessful) {
                val bodyString = response.body?.string().orEmpty()
                val jsonObj = gson.fromJson(bodyString, JsonObject::class.java)
                sha = jsonObj.get("sha")?.asString

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

        // Clean out corrupt streams (about:blank or web pages)
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
            val cleanId = movie.tmdbId.replace("tmdb:", "").replace("movie/", "").replace("tv/", "")
            val keyMovie = "movie/$cleanId"
            val keyClean = cleanId
            val keyTmdb = "tmdb:$cleanId"

            val existingStreams = (cleanedDataMap[movie.tmdbId] ?: cleanedDataMap[keyMovie] ?: cleanedDataMap[keyClean])?.toMutableList() ?: mutableListOf()

            movie.streams.forEach { newStream ->
                val u = newStream.url.lowercase()
                if (u.contains(".m3u8") || u.contains(".mp4")) {
                    if (existingStreams.none { it.url == newStream.url }) {
                        existingStreams.add(newStream)
                    }
                }
            }

            if (existingStreams.isNotEmpty()) {
                val renumberedStreams = existingStreams.mapIndexed { idx, st ->
                    st.copy(
                        name = "Play",
                        title = if (st.title.contains("•")) {
                            val parts = st.title.split("•")
                            val suffix = parts.drop(2).joinToString(" • ").trim()
                            if (suffix.isNotBlank()) "Play • Sunucu ${idx + 1} • $suffix" else "Play • Sunucu ${idx + 1}"
                        } else {
                            "Play • Sunucu ${idx + 1}"
                        }
                    )
                }

                cleanedDataMap[movie.tmdbId] = renumberedStreams
                cleanedDataMap[keyMovie] = renumberedStreams
                cleanedDataMap[keyClean] = renumberedStreams
                cleanedDataMap[keyTmdb] = renumberedStreams
            }
        }

        val updatedJson = gson.toJson(cleanedDataMap)
        val encodedContent = Base64.encodeToString(updatedJson.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)

        val putBody = JsonObject().apply {
            addProperty("message", "Smart merge streams.json (${scrapedMovies.size} filmleri güncellendi)")
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

        client.newCall(putRequest).execute().use { response ->
            return if (response.isSuccessful) {
                Result.success("Toplam ${scrapedMovies.size} film akıllı birleştirmeyle GitHub deponuza yüklendi!")
            } else {
                Result.failure(Exception("GitHub API hatası (streams.json): ${response.code} ${response.message}"))
            }
        }
    }

    private fun updateCatalogOnGitHub(
        githubToken: String,
        repoOwner: String,
        repoName: String,
        scrapedMovies: List<ScrapedMovie>
    ): List<JsonObject> {
        var currentCatalogMetas = mutableListOf<JsonObject>()
        try {
            val apiUrl = "https://api.github.com/repos/$repoOwner/$repoName/contents/data/catalog.json"

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

            scrapedMovies.forEach { movie ->
                val cleanId = movie.tmdbId.replace("tmdb:", "").replace("movie/", "").replace("tv/", "")
                val idStr = if (movie.tmdbId.startsWith("tt") || movie.tmdbId.startsWith("playrepo:")) movie.tmdbId else cleanId

                val genreJsonArray = JsonArray()
                movie.genres.forEach { genreJsonArray.add(it) }

                val catObj = JsonObject().apply {
                    addProperty("id", idStr)
                    addProperty("type", "movie")
                    addProperty("name", movie.title)
                    addProperty("poster", movie.poster.ifBlank { "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png" })
                    addProperty("description", if (!movie.description.isNullOrBlank()) movie.description else "Play • ${movie.streams.size} Sunucu Seçeneği • 1080p TR")
                    if (!movie.year.isNullOrBlank()) {
                        addProperty("releaseInfo", movie.year)
                    }
                    add("genres", genreJsonArray)
                }

                val existingIndex = currentCatalogMetas.indexOfFirst {
                    val itId = it.get("id")?.asString
                    itId == idStr || itId == movie.tmdbId || itId == cleanId || itId == "movie/$cleanId" || itId == "tmdb:$cleanId"
                }

                if (existingIndex >= 0) {
                    currentCatalogMetas[existingIndex] = catObj
                } else {
                    currentCatalogMetas.add(0, catObj)
                }
            }

            val finalWrapper = JsonObject().apply {
                val metasArray = JsonArray()
                currentCatalogMetas.forEach { metasArray.add(it) }
                add("metas", metasArray)
            }

            val updatedCatalogJson = gson.toJson(finalWrapper)
            val encodedContent = Base64.encodeToString(updatedCatalogJson.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)

            val putBody = JsonObject().apply {
                addProperty("message", "Update catalog.json (${scrapedMovies.size} filmleri güncellendi)")
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
        } catch (_: Exception) { }

        return currentCatalogMetas
    }

    private fun updateCategoryCatalogsOnGitHub(
        githubToken: String,
        repoOwner: String,
        repoName: String,
        allMetas: List<JsonObject>
    ) {
        val categoryConfigs = listOf(
            "playrepo/catalog/movie/playrepo-popular.json" to { _: JsonObject -> true },
            "playrepo/catalog/movie/playrepo-catalog-movies.json" to { _: JsonObject -> true },
            "playrepo/catalog/movie/playrepo-aksiyon.json" to { m: JsonObject ->
                hasGenre(m, "aksiyon", "macera")
            },
            "playrepo/catalog/movie/playrepo-komedi.json" to { m: JsonObject ->
                hasGenre(m, "komedi", "mizah")
            },
            "playrepo/catalog/movie/playrepo-dram.json" to { m: JsonObject ->
                hasGenre(m, "dram", "romantik")
            },
            "playrepo/catalog/movie/playrepo-korku.json" to { m: JsonObject ->
                hasGenre(m, "korku", "gerilim")
            },
            "playrepo/catalog/movie/playrepo-bilimkurgu.json" to { m: JsonObject ->
                hasGenre(m, "bilim", "kurgu", "fantastik")
            }
        )

        categoryConfigs.forEach { (filePath, predicate) ->
            try {
                val filtered = allMetas.filter { predicate(it) }
                val targetMetas = if (filePath.contains("popular") || filePath.contains("catalog-movies")) {
                    allMetas.take(60)
                } else {
                    filtered
                }

                if (targetMetas.isEmpty() && !filePath.contains("popular")) {
                    return@forEach
                }

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

                val finalWrapper = JsonObject().apply {
                    val metasArray = JsonArray()
                    targetMetas.forEach { metasArray.add(it) }
                    add("metas", metasArray)
                }

                val updatedJson = gson.toJson(finalWrapper)
                val encodedContent = Base64.encodeToString(updatedJson.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)

                val putBody = JsonObject().apply {
                    addProperty("message", "Sync $filePath with updated categorized movies")
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
            } catch (_: Exception) { }
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

    private fun updateMetaAndStreamsOnGitHub(
        githubToken: String,
        repoOwner: String,
        repoName: String,
        scrapedMovies: List<ScrapedMovie>
    ) {
        scrapedMovies.forEach { movie ->
            val cleanId = movie.tmdbId.replace("tmdb:", "").replace("movie/", "").replace("tv/", "")
            val idStr = if (movie.tmdbId.startsWith("tt") || movie.tmdbId.startsWith("playrepo:")) movie.tmdbId else cleanId
            val safeId = idStr.replace(":", "_")

            // 1. Meta JSON
            val metaWrapper = JsonObject().apply {
                val m = JsonObject().apply {
                    addProperty("id", idStr)
                    addProperty("type", "movie")
                    addProperty("name", movie.title)
                    addProperty("poster", movie.poster.ifBlank { "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png" })
                    addProperty("background", movie.poster.ifBlank { "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png" })
                    addProperty("logo", "https://cdn.jsdelivr.net/gh/csprofesor/apple@main/playrepo/logo.png")
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

                    val bh = JsonObject().apply {
                        addProperty("defaultVideoId", idStr)
                        addProperty("hasScheduledVideos", false)
                    }
                    add("behaviorHints", bh)

                    val videos = JsonArray().apply {
                        val v = JsonObject().apply {
                            addProperty("id", idStr)
                            addProperty("title", movie.title)
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

            // 2. Stream JSON
            val streamWrapper = JsonObject().apply {
                val sArr = JsonArray()
                movie.streams.forEachIndexed { idx, st ->
                    val so = JsonObject().apply {
                        addProperty("name", "Play")
                        val cleanTitle = if (st.title.contains("•")) {
                            val parts = st.title.split("•")
                            val suffix = parts.drop(2).joinToString(" • ").trim()
                            if (suffix.isNotBlank()) "Play • Sunucu ${idx + 1} • $suffix" else "Play • Sunucu ${idx + 1}"
                        } else {
                            "Play • Sunucu ${idx + 1}"
                        }
                        addProperty("title", cleanTitle)
                        addProperty("url", st.url)
                        val bh = JsonObject().apply {
                            addProperty("notSupported", false)
                            val proxyHeaders = JsonObject().apply {
                                val reqObj = JsonObject().apply {
                                    addProperty("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
                                    addProperty("Referer", st.referer?.ifBlank { st.url } ?: st.url)
                                }
                                add("request", reqObj)
                            }
                            add("proxyHeaders", proxyHeaders)
                        }
                        add("behaviorHints", bh)
                    }
                    sArr.add(so)
                }
                add("streams", sArr)
            }

            uploadFileToGitHub(githubToken, repoOwner, repoName, "playrepo/meta/movie/$safeId.json", gson.toJson(metaWrapper))
            uploadFileToGitHub(githubToken, repoOwner, repoName, "playrepo/stream/movie/$safeId.json", gson.toJson(streamWrapper))
            if (idStr != safeId) {
                uploadFileToGitHub(githubToken, repoOwner, repoName, "playrepo/meta/movie/$idStr.json", gson.toJson(metaWrapper))
                uploadFileToGitHub(githubToken, repoOwner, repoName, "playrepo/stream/movie/$idStr.json", gson.toJson(streamWrapper))
            }
        }
    }

    private fun uploadFileToGitHub(
        githubToken: String,
        repoOwner: String,
        repoName: String,
        filePath: String,
        contentJson: String
    ) {
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

            val encodedContent = Base64.encodeToString(contentJson.toByteArray(Charsets.UTF_8), Base64.NO_WRAP)
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
        } catch (_: Exception) { }
    }

    suspend fun publishToGitHub(
        githubToken: String = BuildConfig.GITHUB_TOKEN,
        repoOwner: String = DEFAULT_OWNER,
        repoName: String = DEFAULT_REPO,
        scrapedMovie: ScrapedMovie
    ): Result<String> = publishBatchToGitHub(githubToken, repoOwner, repoName, listOf(scrapedMovie))
}
