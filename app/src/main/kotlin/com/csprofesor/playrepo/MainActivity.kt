package com.csprofesor.playrepo

import android.content.Context
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.addTextChangedListener
import androidx.lifecycle.lifecycleScope
import com.csprofesor.playrepo.databinding.ActivityMainBinding
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val scraperEngine = SiteScraperEngine()
    private val githubPublisher = GitHubPublisher()
    private val gson = Gson()

    private val savedSitesList = mutableListOf<String>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val prefs = getSharedPreferences("playrepo_prefs", Context.MODE_PRIVATE)

        // Load saved sites list or initialize with default
        val savedSitesJson = prefs.getString("saved_sites_json", null)
        if (!savedSitesJson.isNullOrBlank()) {
            try {
                val type = object : TypeToken<MutableList<String>>() {}.type
                val loadedList: MutableList<String>? = gson.fromJson(savedSitesJson, type)
                if (loadedList != null) {
                    savedSitesList.addAll(loadedList)
                }
            } catch (_: Exception) { }
        }

        if (savedSitesList.isEmpty()) {
            savedSitesList.add("https://www.hintfilmizle.com/film-izle/")
            saveSitesListToPrefs()
        }

        updateSavedSitesUI()

        // Load saved TMDB API Key
        binding.etTmdbApiKey.setText(prefs.getString("tmdb_key", ""))
        binding.etTmdbApiKey.addTextChangedListener { text ->
            prefs.edit().putString("tmdb_key", text.toString().trim()).apply()
        }

        binding.btnAddSite.setOnClickListener {
            val newUrl = binding.etNewSiteUrl.text?.toString()?.trim().orEmpty()
            if (newUrl.isBlank() || !newUrl.startsWith("http")) {
                Toast.makeText(this, "Geçerli bir site adresi girin (http/https)!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            if (!savedSitesList.contains(newUrl)) {
                savedSitesList.add(newUrl)
                saveSitesListToPrefs()
                updateSavedSitesUI()
                binding.etNewSiteUrl.setText("")
                Toast.makeText(this, "Yeni site başarıyla eklendi!", Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(this, "Bu site zaten ekli!", Toast.LENGTH_SHORT).show()
            }
        }

        // Master 1-Click Update Button for ALL saved sites
        binding.btnUpdateAllSites.setOnClickListener {
            val tmdbApiKey = binding.etTmdbApiKey.text?.toString()?.trim().orEmpty()
            val maxPagesPerSite = binding.etMaxPages.text?.toString()?.toIntOrNull() ?: 15

            if (savedSitesList.isEmpty()) {
                Toast.makeText(this, "Lütfen önce en az bir film sitesi ekleyin!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            binding.btnUpdateAllSites.isEnabled = false
            binding.btnAddSite.isEnabled = false
            binding.tvStatus.text = "Tüm siteler taranmaya başlanıyor..."

            lifecycleScope.launch {
                val allBatchMovies = mutableListOf<ScrapedMovie>()

                try {
                    savedSitesList.forEachIndexed { siteIdx, siteUrl ->
                        val siteNum = siteIdx + 1
                        val totalSites = savedSitesList.size

                        withContext(Dispatchers.Main) {
                            binding.tvStatus.text = "[$siteNum / $totalSites] Site Taranıyor: $siteUrl"
                        }

                        val siteMovies = scraperEngine.crawlEntireSite(
                            siteUrl = siteUrl,
                            maxPages = maxPagesPerSite,
                            tmdbApiKey = tmdbApiKey,
                            onStatusUpdate = { statusMsg ->
                                withContext(Dispatchers.Main) {
                                    binding.tvStatus.text = "[$siteNum / $totalSites] Site ($siteUrl):\n$statusMsg"
                                }
                            }
                        )

                        allBatchMovies.addAll(siteMovies)
                    }

                    if (allBatchMovies.isEmpty()) {
                        binding.tvStatus.text = "✗ Sitelerden film çekilemedi. Lütfen site adreslerini kontrol edin."
                        return@launch
                    }

                    // Auto-publish batch results to GitHub
                    binding.tvStatus.text = "✓ Toplam ${allBatchMovies.size} film çözüldü! Otomatik GitHub deponuza (`csprofesor/apple`) yükleniyor..."

                    val pubResult = githubPublisher.publishBatchToGitHub(scrapedMovies = allBatchMovies)

                    pubResult.onSuccess { msg ->
                        binding.tvStatus.text = "🎉 $msg\n\nKayıtlı ${savedSitesList.size} sitenin tamamı güncellendi ve Nuvio eklentinize aktarıldı!"
                    }.onFailure { err ->
                        binding.tvStatus.text = "✗ GitHub Yükleme Hatası: ${err.message}"
                    }

                } catch (e: Exception) {
                    binding.tvStatus.text = "Hata oluştu: ${e.message}"
                } finally {
                    binding.btnUpdateAllSites.isEnabled = true
                    binding.btnAddSite.isEnabled = true
                }
            }
        }
    }

    private fun saveSitesListToPrefs() {
        val prefs = getSharedPreferences("playrepo_prefs", Context.MODE_PRIVATE)
        val json = gson.toJson(savedSitesList)
        prefs.edit().putString("saved_sites_json", json).apply()
    }

    private fun updateSavedSitesUI() {
        if (savedSitesList.isEmpty()) {
            binding.tvSavedSites.text = "Kayıtlı site bulunmuyor."
        } else {
            val sb = StringBuilder()
            savedSitesList.forEachIndexed { idx, url ->
                sb.append("${idx + 1}. $url\n")
            }
            binding.tvSavedSites.text = sb.toString().trim()
        }
    }
}
