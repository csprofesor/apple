package com.csprofesor.playrepo

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import com.csprofesor.playrepo.databinding.ActivityMainBinding
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val scraperEngine = SiteScraperEngine()
    private val githubPublisher = GitHubPublisher()

    private val scrapedMoviesList = mutableListOf<ScrapedMovie>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.btnCrawlSite.setOnClickListener {
            val siteUrl = binding.etSiteUrl.text?.toString()?.trim().orEmpty()
            val tmdbApiKey = binding.etTmdbApiKey.text?.toString()?.trim().orEmpty()

            if (siteUrl.isBlank()) {
                Toast.makeText(this, "Lütfen geçerli bir site adresi girin!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            binding.tvStatus.text = "Site taranıyor, film bağlantıları tespit ediliyor..."
            binding.btnCrawlSite.isEnabled = false
            scrapedMoviesList.clear()

            lifecycleScope.launch {
                try {
                    val results = scraperEngine.crawlEntireSite(
                        siteUrl = siteUrl,
                        tmdbApiKey = tmdbApiKey,
                        onProgress = { current, total, movieTitle ->
                            binding.tvStatus.text = "[$current / $total] Taranıyor: $movieTitle"
                        }
                    )

                    scrapedMoviesList.addAll(results)
                    binding.btnPublishBatch.isEnabled = scrapedMoviesList.isNotEmpty()

                    val summary = StringBuilder()
                    summary.append("✓ Site Tarama Tamamlandı!\n")
                    summary.append("Bulunan ve İşlenen Film Sayısı: ${scrapedMoviesList.size}\n\n")
                    scrapedMoviesList.take(15).forEachIndexed { idx, m ->
                        summary.append("${idx + 1}. ${m.title} (TMDB: ${m.tmdbId}) -> ${m.streams.size} kaynak\n")
                    }
                    if (scrapedMoviesList.size > 15) {
                        summary.append("\n... ve ${scrapedMoviesList.size - 15} film daha!")
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
                Toast.makeText(this, "Lütfen GitHub Personal Access Token girin!", Toast.LENGTH_SHORT).show()
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
    }
}
