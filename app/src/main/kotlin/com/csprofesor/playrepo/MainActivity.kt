package com.csprofesor.playrepo

import android.content.Context
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.addTextChangedListener
import androidx.lifecycle.lifecycleScope
import com.csprofesor.playrepo.databinding.ActivityMainBinding
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val scraperEngine = SiteScraperEngine()
    private val githubPublisher = GitHubPublisher()

    private val scrapedMoviesList = mutableListOf<ScrapedMovie>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val prefs = getSharedPreferences("playrepo_prefs", Context.MODE_PRIVATE)

        // Load saved TMDB API Key if exists
        binding.etTmdbApiKey.setText(prefs.getString("tmdb_key", ""))

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

            binding.tvStatus.text = "Sitedeki sayfalar taranıyor, video yayın kaynakları çözülüyor..."
            binding.btnCrawlSite.isEnabled = false
            scrapedMoviesList.clear()

            lifecycleScope.launch {
                try {
                    val results = scraperEngine.crawlEntireSite(
                        siteUrl = siteUrl,
                        maxPages = maxPages,
                        tmdbApiKey = tmdbApiKey,
                        onStatusUpdate = { statusMsg ->
                            withContext(Dispatchers.Main) {
                                binding.tvStatus.text = statusMsg
                            }
                        }
                    )

                    scrapedMoviesList.addAll(results)
                    val hasItems = scrapedMoviesList.isNotEmpty()
                    binding.btnPublishBatch.isEnabled = hasItems

                    val summary = StringBuilder()
                    summary.append("✓ Derin Site Taraması ve Şifre Çözümü Tamamlandı!\n")
                    summary.append("Bulunan ve İşlenen Toplam Film Sayısı: ${scrapedMoviesList.size}\n\n")
                    scrapedMoviesList.take(20).forEachIndexed { idx, m ->
                        summary.append("${idx + 1}. ${m.title} (TMDB: ${m.tmdbId}) -> ${m.streams.size} çözülmüş yayın\n")
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
            if (scrapedMoviesList.isEmpty()) {
                Toast.makeText(this, "Önce siteyi taramalısınız!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            binding.tvStatus.text = "${scrapedMoviesList.size} film doğrudan GitHub deponuza (csprofesor/apple) yükleniyor..."
            binding.btnPublishBatch.isEnabled = false

            lifecycleScope.launch {
                val res = githubPublisher.publishBatchToGitHub(
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
