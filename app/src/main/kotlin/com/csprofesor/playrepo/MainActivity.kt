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

    private var lastScrapedMovie: ScrapedMovie? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.btnScrape.setOnClickListener {
            val movieUrl = binding.etMovieUrl.text?.toString()?.trim().orEmpty()
            val tmdbId = binding.etTmdbId.text?.toString()?.trim().orEmpty()
            val customTitle = binding.etCustomTitle.text?.toString()?.trim().orEmpty()

            if (movieUrl.isBlank()) {
                Toast.makeText(this, "Lütfen geçerli bir film adresi girin!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            binding.tvStatus.text = "Film taranıyor..."
            binding.btnScrape.isEnabled = false

            lifecycleScope.launch {
                try {
                    val result = scraperEngine.scrapeUrl(movieUrl, customTitle, tmdbId)
                    lastScrapedMovie = result
                    binding.btnPublish.isEnabled = true

                    val summary = StringBuilder()
                    summary.append("✓ Tarama Başarılı!\n")
                    summary.append("Başlık: ${result.title}\n")
                    summary.append("TMDB ID: ${result.tmdbId}\n")
                    summary.append("Bulunan Stream Sayısı: ${result.streams.size}\n\n")
                    result.streams.forEachIndexed { idx, st ->
                        summary.append("${idx + 1}. [${st.name}] ${st.url}\n")
                    }

                    binding.tvStatus.text = summary.toString()
                } catch (e: Exception) {
                    binding.tvStatus.text = "Tarama Hatası: ${e.message}"
                } finally {
                    binding.btnScrape.isEnabled = true
                }
            }
        }

        binding.btnPublish.setOnClickListener {
            val movie = lastScrapedMovie
            val token = binding.etGithubToken.text?.toString()?.trim().orEmpty()

            if (movie == null) {
                Toast.makeText(this, "Önce bir film taramalısınız!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            if (token.isBlank()) {
                Toast.makeText(this, "Lütfen GitHub Personal Access Token girin!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            binding.tvStatus.text = "GitHub'a gönderiliyor..."
            binding.btnPublish.isEnabled = false

            lifecycleScope.launch {
                val res = githubPublisher.publishToGitHub(
                    githubToken = token,
                    repoOwner = "csprofesor",
                    repoName = "apple",
                    scrapedMovie = movie
                )

                res.onSuccess { msg ->
                    binding.tvStatus.text = "✓ $msg\nNuvio eklentiniz otomatik güncellendi!"
                }.onFailure { err ->
                    binding.tvStatus.text = "✗ Yayınlama Hatası: ${err.message}"
                }

                binding.btnPublish.isEnabled = true
            }
        }
    }
}
