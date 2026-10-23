package com.csprofesor.playrepo

import android.content.Context
import android.os.Bundle
import android.view.View
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.widget.addTextChangedListener
import androidx.lifecycle.lifecycleScope
import com.csprofesor.playrepo.databinding.ActivityMainBinding
import com.csprofesor.playrepo.databinding.ItemSavedSiteBinding
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

        // Ensure token from BuildConfig is cached locally in device private SharedPreferences
        var savedToken = prefs.getString("github_token", null)
        if (savedToken.isNullOrBlank()) {
            savedToken = BuildConfig.GITHUB_TOKEN
            prefs.edit().putString("github_token", savedToken).apply()
        }

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
        binding.pageSingle.etTmdbApiKey.setText(prefs.getString("tmdb_key", ""))
        binding.pageSingle.etTmdbApiKey.addTextChangedListener { text ->
            prefs.edit().putString("tmdb_key", text.toString().trim()).apply()
        }

        // Bottom Navigation Page Switcher
        binding.bottomNav.setOnItemSelectedListener { item ->
            when (item.itemId) {
                R.id.nav_dashboard -> showPage(1)
                R.id.nav_sites -> showPage(2)
                R.id.nav_single -> showPage(3)
            }
            true
        }

        binding.pageDashboard.btnClearLogs.setOnClickListener {
            logBuilder.clear()
            binding.pageDashboard.tvStatusLog.text = "[READY] System v2.5 Pro ready."
        }

        binding.pageSites.btnResetSites.setOnClickListener {
            savedSitesList.clear()
            savedSitesList.add("https://www.hintfilmizle.com/film-izle/")
            saveSitesListToPrefs()
            updateSavedSitesUI()
            appendLog("[SITE LIST RESET] List reset to default.")
            Toast.makeText(this, "Site listesi varsayılana sıfırlandı!", Toast.LENGTH_SHORT).show()
        }

        // Add & Validate New Site
        binding.pageSites.btnAddSite.setOnClickListener {
            val newUrl = binding.pageSites.etNewSiteUrl.text?.toString()?.trim().orEmpty()
            if (newUrl.isBlank() || !newUrl.startsWith("http")) {
                Toast.makeText(this, "Geçerli bir site adresi girin (http/https)!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            if (savedSitesList.contains(newUrl)) {
                Toast.makeText(this, "Bu site zaten kayıtlı!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            binding.pageSites.btnAddSite.isEnabled = false
            Toast.makeText(this, "Site kontrol ediliyor...", Toast.LENGTH_SHORT).show()
            appendLog("[VALIDATING SITE] $newUrl kontrol ediliyor...")

            lifecycleScope.launch {
                try {
                    val testLinks = scraperEngine.discoverAllMovieLinks(siteUrl = newUrl, maxPages = 1, onPageProgress = { _, _, _ -> })
                    if (testLinks.isNotEmpty()) {
                        savedSitesList.add(newUrl)
                        saveSitesListToPrefs()
                        updateSavedSitesUI()
                        binding.pageSites.etNewSiteUrl.setText("")
                        appendLog("✓ [SITE VERIFIED] $newUrl doğrulandı (${testLinks.size} film bulundu) ve eklendi!")
                        Toast.makeText(this@MainActivity, "Site başarıyla doğrulandı ve eklendi!", Toast.LENGTH_SHORT).show()
                    } else {
                        // Add anyway as fallback
                        savedSitesList.add(newUrl)
                        saveSitesListToPrefs()
                        updateSavedSitesUI()
                        binding.pageSites.etNewSiteUrl.setText("")
                        appendLog("[SITE ADDED] $newUrl eklendi.")
                        Toast.makeText(this@MainActivity, "Site listeye eklendi.", Toast.LENGTH_SHORT).show()
                    }
                } catch (_: Exception) {
                    savedSitesList.add(newUrl)
                    saveSitesListToPrefs()
                    updateSavedSitesUI()
                    binding.pageSites.etNewSiteUrl.setText("")
                    appendLog("[SITE ADDED] $newUrl eklendi.")
                } finally {
                    binding.pageSites.btnAddSite.isEnabled = true
                }
            }
        }

        // 1. SINGLE SITE / PAGE CRAWL
        binding.pageSingle.btnCrawlSingle.setOnClickListener {
            val singleUrl = binding.pageSingle.etSingleUrl.text?.toString()?.trim().orEmpty()
            val tmdbApiKey = binding.pageSingle.etTmdbApiKey.text?.toString()?.trim().orEmpty()
            val maxPages = binding.pageSingle.etMaxPages.text?.toString()?.toIntOrNull() ?: 20

            if (singleUrl.isBlank()) {
                Toast.makeText(this, "Lütfen bir site/film adresi girin!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            setControlsEnabled(false)
            showPage(1)
            appendLog("[SINGLE CRAWL START] $singleUrl (Limit: $maxPages sayfa)")

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
                        appendLog("[PUBLISHING] ${siteMovies.size} çözülmüş film GitHub'a yükleniyor...")
                        val activeToken = prefs.getString("github_token", BuildConfig.GITHUB_TOKEN) ?: BuildConfig.GITHUB_TOKEN
                        val pubRes = githubPublisher.publishBatchToGitHub(githubToken = activeToken, scrapedMovies = siteMovies)
                        pubRes.onSuccess { msg ->
                            appendLog("[SUCCESS] $msg")
                            binding.pageDashboard.tvStatTotalMovies.text = siteMovies.size.toString()
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

        // 2. MASTER 1-CLICK ALL SITES UPDATE WITH SMART MERGE
        binding.pageDashboard.btnUpdateAllSites.setOnClickListener {
            val tmdbApiKey = binding.pageSingle.etTmdbApiKey.text?.toString()?.trim().orEmpty()
            val maxPagesPerSite = binding.pageDashboard.etMaxPagesDashboard.text?.toString()?.toIntOrNull() ?: 20

            if (savedSitesList.isEmpty()) {
                Toast.makeText(this, "Kayıtlı site bulunmuyor!", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            setControlsEnabled(false)
            appendLog("[MASTER UPDATE START] ${savedSitesList.size} kayıtlı sitenin tamamı taranıyor (Limit: site başı $maxPagesPerSite sayfa)...")

            lifecycleScope.launch {
                val allBatchMovies = mutableListOf<ScrapedMovie>()

                try {
                    savedSitesList.forEachIndexed { siteIdx, siteUrl ->
                        val siteNum = siteIdx + 1
                        val totalSites = savedSitesList.size

                        withContext(Dispatchers.Main) {
                            appendLog("[SITE $siteNum/$totalSites] Taranıyor & Şifreler Çözülüyor: $siteUrl")
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

                    appendLog("[PUBLISHING] Toplam ${allBatchMovies.size} film çözüldü! GitHub deponuza (`csprofesor/apple`) gönderiliyor...")

                    val activeToken = prefs.getString("github_token", BuildConfig.GITHUB_TOKEN) ?: BuildConfig.GITHUB_TOKEN
                    val pubResult = githubPublisher.publishBatchToGitHub(githubToken = activeToken, scrapedMovies = allBatchMovies)

                    pubResult.onSuccess { msg ->
                        appendLog("🎉 [SUCCESS] $msg")
                        appendLog("[COMPLETED] Nuvio eklentiniz akıllı birleştirmeyle güncellendi!")
                        binding.pageDashboard.tvStatTotalMovies.text = allBatchMovies.size.toString()
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

    private fun showPage(pageNumber: Int) {
        binding.pageDashboard.root.visibility = if (pageNumber == 1) View.VISIBLE else View.GONE
        binding.pageSites.root.visibility = if (pageNumber == 2) View.VISIBLE else View.GONE
        binding.pageSingle.root.visibility = if (pageNumber == 3) View.VISIBLE else View.GONE
    }

    private fun setControlsEnabled(enabled: Boolean) {
        binding.pageDashboard.btnUpdateAllSites.isEnabled = enabled
        binding.pageSingle.btnCrawlSingle.isEnabled = enabled
        binding.pageSites.btnAddSite.isEnabled = enabled
    }

    private fun appendLog(msg: String) {
        val time = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date())
        logBuilder.append("[$time] $msg\n")
        binding.pageDashboard.tvStatusLog.text = logBuilder.toString()
        binding.pageDashboard.svLogContainer.post {
            binding.pageDashboard.svLogContainer.fullScroll(View.FOCUS_DOWN)
        }
    }

    private fun saveSitesListToPrefs() {
        val prefs = getSharedPreferences("playrepo_prefs", Context.MODE_PRIVATE)
        val json = gson.toJson(savedSitesList)
        prefs.edit().putString("saved_sites_json", json).apply()
    }

    private fun updateSavedSitesUI() {
        binding.pageDashboard.tvStatActiveSites.text = savedSitesList.size.toString()
        binding.pageSites.llSavedSitesContainer.removeAllViews()

        if (savedSitesList.isEmpty()) {
            val emptyTv = TextView(this).apply {
                text = "Kayıtlı site bulunmuyor."
                setTextColor(0xFF888888.toInt())
                textSize = 14f
            }
            binding.pageSites.llSavedSitesContainer.addView(emptyTv)
        } else {
            savedSitesList.forEachIndexed { idx, url ->
                val itemBinding = ItemSavedSiteBinding.inflate(layoutInflater, binding.pageSites.llSavedSitesContainer, false)
                itemBinding.tvSiteIndex.text = "${idx + 1}."
                itemBinding.tvSiteUrl.text = url

                itemBinding.btnDeleteSite.setOnClickListener {
                    savedSitesList.removeAt(idx)
                    saveSitesListToPrefs()
                    updateSavedSitesUI()
                    appendLog("[SITE REMOVED] $url")
                    Toast.makeText(this@MainActivity, "Site silindi: $url", Toast.LENGTH_SHORT).show()
                }

                binding.pageSites.llSavedSitesContainer.addView(itemBinding.root)
            }
        }
    }
}
