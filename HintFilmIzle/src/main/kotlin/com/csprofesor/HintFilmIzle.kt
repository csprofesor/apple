package com.csprofesor

import com.lagradost.cloudstream3.MainAPI
import com.lagradost.cloudstream3.TvType

class HintFilmIzle : MainAPI() {
    override var mainUrl = "https://www.hintfilmizle.com"
    override var name = "HintFilmİzle"
    override var lang = "tr"
    override val supportedTypes = setOf(TvType.Movie)
    override val hasMainPage = false
}
