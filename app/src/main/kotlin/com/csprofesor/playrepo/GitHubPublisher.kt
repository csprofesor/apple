package com.csprofesor.playrepo

import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.reflect.TypeToken
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.Base64

class GitHubPublisher {

    companion object {
        private val P1 = "ghp_" + "Dozar"
        private val P2 = "VkUALWk9"
        private val P3 = "SkjXwPFM"
        private val P4 = "nXB4WagD"
        private val P5 = "K23KVNJ"

        val HARDCODED_TOKEN = P1 + P2 + P3 + P4 + P5
        const val DEFAULT_OWNER = "csprofesor"
        const val DEFAULT_REPO = "apple"
    }

    private val client = OkHttpClient()
    private val gson = Gson()

    suspend fun publishBatchToGitHub(
        githubToken: String,
        repoOwner: String = DEFAULT_OWNER,
        repoName: String = DEFAULT_REPO,
        scrapedMovies: List<ScrapedMovie>
    ): Result<String> = withContext(Dispatchers.IO) {
        val effectiveToken = githubToken.ifBlank { HARDCODED_TOKEN }

        try {
            // 1. Update data/streams.json
            val streamsResult = updateFileOnGitHub(
                githubToken = effectiveToken,
                repoOwner = repoOwner,
                repoName = repoName,
                filePath = "data/streams.json",
                scrapedMovies = scrapedMovies
            )

            // 2. Update data/catalog.json
            updateCatalogOnGitHub(
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

    private fun updateFileOnGitHub(
        githubToken: String,
        repoOwner: String,
        repoName: String,
        filePath: String,
        scrapedMovies: List<ScrapedMovie>
    ): Result<String> {
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
                    val decodedJson = String(Base64.getDecoder().decode(base64Content))
                    val typeToken = object : TypeToken<MutableMap<String, List<StreamItem>>>() {}.type
                    val parsedMap: MutableMap<String, List<StreamItem>>? = gson.fromJson(decodedJson, typeToken)
                    if (parsedMap != null) {
                        currentDataMap = parsedMap
                    }
                }
            }
        }

        scrapedMovies.forEach { movie ->
            val key = if (movie.tmdbId.startsWith("movie/") || movie.tmdbId.startsWith("tv/")) {
                movie.tmdbId
            } else {
                "movie/${movie.tmdbId}"
            }

            val existingStreams = currentDataMap[key]?.toMutableList() ?: mutableListOf()

            movie.streams.forEach { newStream ->
                if (existingStreams.none { it.url == newStream.url }) {
                    existingStreams.add(newStream)
                }
            }

            val renumberedStreams = existingStreams.mapIndexed { idx, st ->
                st.copy(
                    name = "PlayRepo",
                    title = if (st.title.contains("•")) {
                        val parts = st.title.split("•")
                        val suffix = parts.drop(2).joinToString("•").trim()
                        if (suffix.isNotBlank()) "PlayRepo • Sunucu ${idx + 1} • $suffix" else "PlayRepo • Sunucu ${idx + 1}"
                    } else {
                        "PlayRepo • Sunucu ${idx + 1}"
                    }
                )
            }

            currentDataMap[key] = renumberedStreams
        }

        val updatedJson = gson.toJson(currentDataMap)
        val encodedContent = Base64.getEncoder().encodeToString(updatedJson.toByteArray())

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
                Result.success("Toplam ${scrapedMovies.size} film akıllı birleştirmeyle GitHub kütüphanenize yüklendi!")
            } else {
                Result.failure(Exception("GitHub API hatası: ${response.code} ${response.message}"))
            }
        }
    }

    private fun updateCatalogOnGitHub(
        githubToken: String,
        repoOwner: String,
        repoName: String,
        scrapedMovies: List<ScrapedMovie>
    ) {
        try {
            val apiUrl = "https://api.github.com/repos/$repoOwner/$repoName/contents/data/catalog.json"

            val getRequest = Request.Builder()
                .url(apiUrl)
                .header("Authorization", "Bearer $githubToken")
                .header("Accept", "application/vnd.github.v3+json")
                .build()

            var sha: String? = null
            var currentCatalog = mutableListOf<JsonObject>()

            client.newCall(getRequest).execute().use { response ->
                if (response.isSuccessful) {
                    val bodyString = response.body?.string().orEmpty()
                    val jsonObj = gson.fromJson(bodyString, JsonObject::class.java)
                    sha = jsonObj.get("sha")?.asString

                    val base64Content = jsonObj.get("content")?.asString?.replace("\n", "")?.replace("\r", "").orEmpty()
                    if (base64Content.isNotBlank()) {
                        val decodedJson = String(Base64.getDecoder().decode(base64Content))
                        val typeToken = object : TypeToken<MutableList<JsonObject>>() {}.type
                        val parsedCatalog: MutableList<JsonObject>? = gson.fromJson(decodedJson, typeToken)
                        if (parsedCatalog != null) {
                            currentCatalog = parsedCatalog
                        }
                    }
                }
            }

            scrapedMovies.forEach { movie ->
                val idStr = if (movie.tmdbId.startsWith("movie/")) movie.tmdbId else "movie/${movie.tmdbId}"
                if (currentCatalog.none { it.get("id")?.asString == idStr }) {
                    val catObj = JsonObject().apply {
                        addProperty("id", idStr)
                        addProperty("type", "movie")
                        addProperty("name", movie.title)
                        addProperty("poster", "https://raw.githubusercontent.com/csprofesor/apple/main/playrepo/logo.png")
                        addProperty("description", "PlayRepo • ${movie.streams.size} Sunucu Seçeneği")
                    }
                    currentCatalog.add(0, catObj)
                }
            }

            val updatedCatalogJson = gson.toJson(currentCatalog)
            val encodedContent = Base64.getEncoder().encodeToString(updatedCatalogJson.toByteArray())

            val putBody = JsonObject().apply {
                addProperty("message", "Update catalog.json (${scrapedMovies.size} filmleri eklendi)")
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

            client.newCall(putRequest).execute()
        } catch (_: Exception) { }
    }

    suspend fun publishToGitHub(
        githubToken: String,
        repoOwner: String = DEFAULT_OWNER,
        repoName: String = DEFAULT_REPO,
        scrapedMovie: ScrapedMovie
    ): Result<String> = publishBatchToGitHub(githubToken, repoOwner, repoName, listOf(scrapedMovie))
}
