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

    private val client = OkHttpClient()
    private val gson = Gson()

    suspend fun publishToGitHub(
        githubToken: String,
        repoOwner: String,
        repoName: String,
        scrapedMovie: ScrapedMovie
    ): Result<String> = withContext(Dispatchers.IO) {
        try {
            val apiUrl = "https://api.github.com/repos/$repoOwner/$repoName/contents/data/streams.json"

            // 1. Get current streams.json from GitHub if it exists
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

            // 2. Add or update the movie entry in data map
            val key = if (scrapedMovie.tmdbId.startsWith("movie/") || scrapedMovie.tmdbId.startsWith("tv/")) {
                scrapedMovie.tmdbId
            } else {
                "movie/${scrapedMovie.tmdbId}"
            }

            currentDataMap[key] = scrapedMovie.streams

            // 3. Serialize back to formatted JSON
            val updatedJson = gson.toJson(currentDataMap)
            val encodedContent = Base64.getEncoder().encodeToString(updatedJson.toByteArray())

            // 4. Push update to GitHub API
            val putBody = JsonObject().apply {
                addProperty("message", "Update streams.json for ${scrapedMovie.title} (${scrapedMovie.tmdbId})")
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
                if (response.isSuccessful) {
                    Result.success("Başarıyla GitHub'a yayınlandı! ($key)")
                } else {
                    Result.failure(Exception("GitHub API hatası: ${response.code} ${response.message}"))
                }
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
}
