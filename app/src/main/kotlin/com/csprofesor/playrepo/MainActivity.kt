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
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val scraperEngine = SiteScraperEngine()
    private val githubPublisher = GitHubPublisher()
    private val gson = Gson()

    private val savedSitesList = mutableListOf<String>()
    private val logBuilder = StringBuilder()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        val prefs = getSharedPreferences("playrepo_prefs", Context.MODE_PRIVATE)

        // 1. Load saved sites list or default
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

        binding.btnClearLogs.setOnClickListener {
            logBuilder.clear()
            binding.tvStatusLog.text = "[READY] System reset."
        }

        // Add New Site
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
                appendLog("[SITE ADDED] $newUrl")
                Toast.makeText(this, "Yeni site eklendi!", Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(this, "Bu site zaten kayıtlı!", Toast.LENGTH_SHORT).show()
            }
        }

        // 1. SINGLE SITE / PAGE CRAWL
        binding.btnCrawlSingle.setOnClickListener {
            val singleUrl = binding.etSingleUrl.text?.toString()?.trim().orEmpty()
            val tmdbApiKey = binding.etTmdbApiKey.text?.toString()?.trim().orEmpty()
            val maxPages = binding.etMaxPages.text?.toString()?.toIntOrNull() ?: 20

            if (singleUrl.isBlank()) {
                Toast.makeText(this, "Lütfen bir site/film adresi girin!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            setControlsEnabled(false)
            appendLog("[SINGLE CRAWL START] $singleUrl")

            lifecycleScope.launch {
                try {
                    val siteMovies = scraperEngine.crawlEntireSite(
                        siteUrl = singleUrl,
                        maxPages = maxPages,
                        tmdbApiKey = tmdbApiKey,
                        onStatusUpdate = { status ->
                            withContext(Dispatchers.Main) {
                                appendLog(status)
                            }
                        }
                    )

                    if (siteMovies.isNotEmpty()) {
                        appendLog("[PUBLISHING] ${siteMovies.size} filmleri GitHub'a yükleniyor...")
                        val pubRes = githubPublisher.publishBatchToGitHub(scrapedMovies = siteMovies)
                        pubRes.onSuccess { msg ->
                            appendLog("[SUCCESS] $msg")
                            binding.tvStatTotalMovies.text = siteMovies.size.toString()
                        }.onFailure { err ->
                            appendLog("[ERROR] GitHub yükleme hatası: ${err.message}")
                        }
                    } else {
                        appendLog("[WARNING] Bu adresten film çekilemedi.")
                    }
                } catch (e: Exception) {
                    appendLog("[ERROR] $e")
                } finally {
                    setControlsEnabled(true)
                }
            }
        }

        // 2. MASTER 1-CLICK ALL SITES UPDATE
        binding.btnUpdateAllSites.setOnClickListener {
            val tmdbApiKey = binding.etTmdbApiKey.text?.toString()?.trim().orEmpty()
            val maxPagesPerSite = binding.etMaxPages.text?.toString()?.toIntOrNull() ?: 20

            if (savedSitesList.isEmpty()) {
                Toast.makeText(this, "Kayıtlı site bulunmuyor!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            setControlsEnabled(false)
            appendLog("[MASTER UPDATE START] ${savedSitesList.size} kayıtlı sitenin tamamı taranıyor...")

            lifecycleScope.launch {
                val allBatchMovies = mutableListOf<ScrapedMovie>()

                try {
                    savedSitesList.forEachIndexed { siteIdx, siteUrl ->
                        val siteNum = siteIdx + 1
                        val totalSites = savedSitesList.size

                        withContext(Dispatchers.Main) {
                            appendLog("[SITE $siteNum/$totalSites] Taranıyor: $siteUrl")
                        }

                        val siteMovies = scraperEngine.crawlEntireSite(
                            siteUrl = siteUrl,
                            maxPages = maxPagesPerSite,
                            tmdbApiKey = tmdbApiKey,
                            onStatusUpdate = { statusMsg ->
                                withContext(Dispatchers.Main) {
                                    appendLog(" -> $statusMsg")
                                }
                            }
                        )

                        allBatchMovies.addAll(siteMovies)
                    }

                    if (allBatchMovies.isEmpty()) {
                        appendLog("[ERROR] Sitelerden film çekilemedi.")
                        return@launch
                    }

                    appendLog("[PUBLISHING] Toplam ${allBatchMovies.size} film çözüldü! GitHub'a (`csprofesor/apple`) gönderiliyor...")

                    val pubResult = githubPublisher.publishBatchToGitHub(scrapedMovies = allBatchMovies)

                    pubResult.onSuccess { msg ->
                        appendLog("🎉 [SUCCESS] $msg")
                        appendLog("[COMPLETED] Nuvio eklentiniz tamamen güncellendi!")
                        binding.tvStatTotalMovies.text = allBatchMovies.size.toString()
                    }.onFailure { err ->
                        appendLog("✗ [ERROR] GitHub Yükleme Hatası: ${err.message}")
                    }

                } catch (e: Exception) {
                    appendLog("[ERROR] $e")
                } finally {
                    setControlsEnabled(true)
                }
            }
        }
    }

    private fun setControlsEnabled(enabled: Boolean) {
        binding.btnUpdateAllSites.isEnabled = enabled
        binding.btnCrawlSingle.isEnabled = enabled
        binding.btnAddSite.isEnabled = enabled
    }

    private fun appendLog(msg: String) {
        val time = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
        logBuilder.append("[$time] $msg\n")
        binding.tvStatusLog.text = logBuilder.toString()
        binding.svLogContainer.post {
            binding.svLogContainer.fullScroll(android.view.View.FOCUS_DOWN)
        }
    }

    private fun saveSitesListToPrefs() {
        val prefs = getSharedPreferences("playrepo_prefs", Context.MODE_PRIVATE)
        val json = gson.toJson(savedSitesList)
        prefs.edit().putString("saved_sites_json", json).apply()
    }

    private fun updateSavedSitesUI() {
        binding.tvStatActiveSites.text = savedSitesList.size.toString()
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
