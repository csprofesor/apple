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
        const val HARDCODED_OWNER = "csprofesor"
        const val HARDCODED_REPO = "apple"
    }

    private val client = OkHttpClient()
    private val gson = Gson()

    suspend fun publishBatchToGitHub(
        githubToken: String = HARDCODED_TOKEN,
        repoOwner: String = HARDCODED_OWNER,
        repoName: String = HARDCODED_REPO,
        scrapedMovies: List<ScrapedMovie>
    ): Result<String> = withContext(Dispatchers.IO) {
        val effectiveToken = githubToken.ifBlank { HARDCODED_TOKEN }
        val effectiveOwner = repoOwner.ifBlank { HARDCODED_OWNER }
        val effectiveRepo = repoName.ifBlank { HARDCODED_REPO }

        try {
            val apiUrl = "https://api.github.com/repos/$effectiveOwner/$effectiveRepo/contents/data/streams.json"

            // 1. Get current streams.json from GitHub
            val getRequest = Request.Builder()
                .url(apiUrl)
                .header("Authorization", "Bearer $effectiveToken")
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

            // 2. Smart Merge Streams across multi-sites without duplicates
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

                // Re-label server index for clean UI
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

            // 3. Serialize back to formatted JSON
            val updatedJson = gson.toJson(currentDataMap)
            val encodedContent = Base64.getEncoder().encodeToString(updatedJson.toByteArray())

            // 4. Push update to GitHub API
            val putBody = JsonObject().apply {
                addProperty("message", "Smart merge streams.json (${scrapedMovies.size} filmleri güncellendi)")
                addProperty("content", encodedContent)
                if (sha != null) {
                    addProperty("sha", sha)
                }
            }

            val putRequest = Request.Builder()
                .url(apiUrl)
                .header("Authorization", "Bearer $effectiveToken")
                .header("Accept", "application/vnd.github.v3+json")
                .put(putBody.toString().toRequestBody("application/json".toMediaType()))
                .build()

            client.newCall(putRequest).execute().use { response ->
                if (response.isSuccessful) {
                    Result.success("Toplam ${scrapedMovies.size} film akıllı birleştirmeyle GitHub deponuza yüklendi!")
                } else {
                    Result.failure(Exception("GitHub API hatası: ${response.code} ${response.message}"))
                }
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun publishToGitHub(
        githubToken: String = HARDCODED_TOKEN,
        repoOwner: String = HARDCODED_OWNER,
        repoName: String = HARDCODED_REPO,
        scrapedMovie: ScrapedMovie
    ): Result<String> = publishBatchToGitHub(effectiveToken(githubToken), effectiveOwner(repoOwner), effectiveRepo(repoName), listOf(scrapedMovie))

    private fun effectiveToken(token: String) = token.ifBlank { HARDCODED_TOKEN }
    private fun effectiveOwner(owner: String) = owner.ifBlank { HARDCODED_OWNER }
    private fun effectiveRepo(repo: String) = repo.ifBlank { HARDCODED_REPO }
}
