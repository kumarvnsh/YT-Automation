# World history sources and establishing maps

Institutional entry points checked on 2026-10-22. Approval means these domains may
supply research leads; it does not automatically verify every assertion, make
archival accounts neutral, or license images. Cite the specific collection item or
article supporting each claim, preserve its date/context, and corroborate disputed
or consequential claims with independent scholarship. Check each item's reuse
terms separately.

| Coverage | Domain | Verified institutional entry point |
| --- | --- | --- |
| China / imperial material culture | dpm.org.cn | [Palace Museum collections](https://intl.dpm.org.cn/collection_list); [official multilingual website description](https://www.dpm.org.cn/bottom/friend.html) |
| China / historical material culture | chnmuseum.cn | [National Museum of China institutional identity](https://en.chnmuseum.cn/about_the_nmc_593/about_the_nmc_594/); [exhibitions and collections](https://en.chnmuseum.cn/) |
| India | nationalarchives.nic.in | [National Archives of India, Government of India](https://www.nationalarchives.nic.in/en) |
| Japan | nich.go.jp | [National Institutes for Cultural Heritage](https://www.nich.go.jp/en/); [Museum of the Imperial Collections](https://shozokan.nich.go.jp/en/about) |
| South Africa | nationalarchives.gov.za | [National Archives and Records Service](https://www.nationalarchives.gov.za/) |
| Kenya / East Africa | museums.or.ke | [National Museums of Kenya: institutional remit](https://museums.or.ke/about-us/) |
| Russia / museum collections | hermitagemuseum.org | [State Hermitage Museum: institutional identity](https://hermitagemuseum.org/about?lng=en); [collection history](https://hermitagemuseum.org/panorama/virtual_visit/new_hermitage/history/history_2/?lng=en) |
| Gulf / Middle East | qdl.qa | [Qatar Digital Library: archive scope and institutional partnership](https://www.qdl.qa/en/about) |
| Gulf / Middle East | qnl.qa | [Qatar National Library: QDL heritage collections](https://qnl.qa/en/about/news/new-qatar-digital-library-offers-readers-unrivalled-collection-precious-heritage) |

These are a starting set, not continent-wide coverage. Africa and the Middle East
are not single historical cultures; use country, locality, period, language and
community-specific sources for the actual story. Museum object descriptions and
state archives should not be treated as arbiters of contested modern sovereignty.

## Map conventions

`scripts/build_world_maps.py` selects geometry from the same pinned
[Natural Earth v5.1.2 country dataset](https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_admin_0_countries.geojson)
as the original country assets. Both map builders preserve unrelated existing manifest entries. The manifest records
the source bytes' SHA-256, selected feature names, asset checksum, extent,
projection, license and scope. The new SVGs preserve polygon rings, including
holes, and use only paths and accessible text. Russia is unwrapped across the
dateline by shifting negative longitudes +360; this changes display coordinates,
not geography. Natural Earth's low-resolution small islands may be omitted by
the source dataset.

All assets are **present-day establishing geography using the pinned dataset's
boundary conventions**, not period maps. Do not imply they show the boundaries
of an ancient kingdom, colonial administration, empire, or historical event.
Natural Earth uses de facto boundaries; displaying its geometry is not a claim of
legal sovereignty or universal recognition, particularly for disputed territories.
The pin predates current events; maps are neither live territorial updates nor
survey-grade data. Use separately vetted period-specific assets when borders are
material to narration.

`africa` selects every source feature whose `CONTINENT` is `Africa`, retaining
source administrative divisions and disputed-territory features. `middle_east`
is an editorial establishing convention: Bahrain, Cyprus, Egypt, Iran, Iraq,
Israel, Jordan, Kuwait, Lebanon, Oman, Palestine, Qatar, Saudi Arabia, Syria,
Turkey, United Arab Emirates and Yemen. It includes Egypt, Cyprus and Turkey,
excludes North Africa west of Egypt and the South Caucasus, and makes no claim
that the region has a universally agreed boundary. Its exact source selections
are recorded in `feature_names`; tiny countries absent from the 110m source
cannot be fabricated into the geometry.

Use `src.map_assets.available_map_ids()` for manifest-derived available IDs;
`load_map()` verifies the asset checksum and sanitizes the SVG before use.

China is explicitly supported through the `china` establishing asset (source
feature `ADMIN=China`). It retains the same pinned dataset's de facto boundary
conventions and disputed-territory limitations described above; it is not a
historical territorial claim. Chinese collection sources can support carefully
attributed stories about everyday origins, inventions, food, clothing, ceramics,
writing and distinct regional cultures and periods. Check claims of "first" or
sole invention independently, and distinguish surviving objects from proof of
origin. Museum institutional narratives do not independently settle contested
historical or modern political assertions. Inclusion does not guarantee audience
reach or interest.

## Additional collection leads

- `vam.ac.uk`: [V&A Indian textiles](https://www.vam.ac.uk/articles/indian-textiles/) and [Indian chintz](https://www.vam.ac.uk/blog/fabric-of-india/guest-post-indian-chintz-a-legacy-of-luxury). Start from retrieved collection evidence for everyday-origin stories; do not generalize individual items into disputed first-invention claims.
- `britishmuseum.org`: [British Museum Indian chess-piece collection record](https://www.britishmuseum.org/collection/object/A_As1880-0820-31). An object record supports its own catalogued context, not all claims about the invention of chess.

Research prompts enumerate the exact approved host list and request explicit `site:HOST` searches in initial research, citation repair and replacement packets. Search results outside that list remain rejected even if the search provider returns them.
