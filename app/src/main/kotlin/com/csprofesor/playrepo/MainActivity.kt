package com.csprofesor.playrepo

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.addTextChangedListener
import androidx.lifecycle.lifecycleScope
import com.csprofesor.playrepo.databinding.ActivityMainBinding
import com.google.gson.Gson
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val scraperEngine = SiteScraperEngine()
    private val githubPublisher = GitHubPublisher()
    private val gson = Gson()

    private val scrapedMoviesList = mutableListOf<ScrapedMovie>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val prefs = getSharedPreferences("playrepo_prefs", MODE_PRIVATE)

        // Load saved Token & TMDB API Key
        binding.etGithubToken.setText(prefs.getString("github_token", ""))
        binding.etTmdbApiKey.setText(prefs.getString("tmdb_key", ""))

        binding.etGithubToken.addTextChangedListener { text ->
            prefs.edit().putString("github_token", text.toString().trim()).apply()
        }

        binding.etTmdbApiKey.addTextChangedListener { text ->
            prefs.edit().putString("tmdb_key", text.toString().trim()).apply()
        }

        binding.btnCrawlSite.setOnClickListener {
            val siteUrl = binding.etSiteUrl.text?.toString()?.trim().orEmpty()
            val tmdbApiKey = binding.etTmdbApiKey.text?.toString()?.trim().orEmpty()
            val maxPages = binding.etMaxPages.text?.toString()?.toIntOrNull() ?: 30

            if (siteUrl.isBlank()) {
                Toast.makeText(this, "Lütfen geçerli bir site adresi girin!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            binding.tvStatus.text = "Sitedeki sayfalar bulunuyor, derin tarama başlatılıyor..."
            binding.btnCrawlSite.isEnabled = false
            scrapedMoviesList.clear()

            lifecycleScope.launch {
                try {
                    val results = scraperEngine.crawlEntireSite(
                        siteUrl = siteUrl,
                        maxPages = maxPages,
                        tmdbApiKey = tmdbApiKey,
                        onStatusUpdate = { status ->
                            binding.tvStatus.text = status
                        }
                    )

                    scrapedMoviesList.addAll(results)
                    val hasItems = scrapedMoviesList.isNotEmpty()
                    binding.btnPublishBatch.isEnabled = hasItems
                    binding.btnCopyJson.isEnabled = hasItems

                    val summary = StringBuilder()
                    summary.append("✓ Derin Site Taraması Tamamlandı!\n")
                    summary.append("Bulunan ve İşlenen Toplam Film Sayısı: ${scrapedMoviesList.size}\n\n")
                    scrapedMoviesList.take(20).forEachIndexed { idx, m ->
                        summary.append("${idx + 1}. ${m.title} (TMDB: ${m.tmdbId}) -> ${m.streams.size} kaynak\n")
                    }
                    if (scrapedMoviesList.size > 20) {
                        summary.append("\n... ve ${scrapedMoviesList.size - 20} film daha!")
                    }

                    binding.tvStatus.text = summary.toString()
                } catch (e: Exception) {
                    binding.tvStatus.text = "Tarama Hatası: ${e.message}"
                } finally {
                    binding.btnCrawlSite.isEnabled = true
                }
            }
        }

        binding.btnPublishBatch.setOnClickListener {
            val token = binding.etGithubToken.text?.toString()?.trim().orEmpty()

            if (scrapedMoviesList.isEmpty()) {
                Toast.makeText(this, "Önce siteyi taramalısınız!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            if (token.isBlank()) {
                Toast.makeText(this, "Lütfen GitHub Token girin veya 'JSON'u Kopyala' butonunu kullanın!", Toast.LENGTH_LONG).show()
                return@setOnClickListener
            }

            binding.tvStatus.text = "${scrapedMoviesList.size} film GitHub'a yükleniyor..."
            binding.btnPublishBatch.isEnabled = false

            lifecycleScope.launch {
                val res = githubPublisher.publishBatchToGitHub(
                    githubToken = token,
                    repoOwner = "csprofesor",
                    repoName = "apple",
                    scrapedMovies = scrapedMoviesList
                )

                res.onSuccess { msg ->
                    binding.tvStatus.text = "✓ $msg\nNuvio eklentiniz otomatik güncellendi!"
                }.onFailure { err ->
                    binding.tvStatus.text = "✗ Yayınlama Hatası: ${err.message}"
                }

                binding.btnPublishBatch.isEnabled = true
            }
        }

        binding.btnCopyJson.setOnClickListener {
            if (scrapedMoviesList.isEmpty()) {
                Toast.makeText(this, "Önce siteyi taramalısınız!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            val dataMap = mutableMapOf<String, List<StreamItem>>()
            scrapedMoviesList.forEach { movie ->
                val key = if (movie.tmdbId.startsWith("movie/") || movie.tmdbId.startsWith("tv/")) {
                    movie.tmdbId
                } else {
                    "movie/${movie.tmdbId}"
                }
                dataMap[key] = movie.streams
            }

            val jsonString = gson.toJson(dataMap)

            val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            val clip = ClipData.newPlainText("PlayRepo JSON", jsonString)
            clipboard.setPrimaryClip(clip)

            Toast.makeText(this, "JSON panoya kopyalandı! GitHub Web sitesine yapıştırabilirsiniz.", Toast.LENGTH_LONG).show()
            binding.tvStatus.text = "✓ JSON kopyalandı!\nGitHub'a girip data/streams.json dosyasına yapıştırabilirsiniz."
        }
    }
}
