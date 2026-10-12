# MorrowindRuntime opcode test plan — Morrowind.esm + TR_Mainland.esm

**Tool:** `python -m tools.dialog.morrowind_opcode_testplan --plugin Morrowind.esm TR_Mainland.esm --stubs --markdown <this file>`

Measured over 1951 journal quest(s) with stages. Ranked by new commands per STAGE the tester must play, so short quests come first. **A prerequisite is its own row**, marked in *Why*, and its stages and commands both count -- the table is the whole play order, top to bottom.

## Ported commands (118 of 132 covered)

| # | Quest | Quest giver | `coc` target / location | Stages | Why | Commands it is first to test |
|--:|---|---|---|--:|---|---|
| 1 | Temple: Armun Ashlands Avenger | TR_m4_D_KhirakaiBandit1 (TR_m4_TT_ArmunAdvC_sc) | Khirakai | 2 | — | `activate`, `additem`, `addspell`, `cellchanged`, `disable`, `equip`, `getcurrentweather`, `getdisabled`, `getjournalindex`, `getsecondspassed`, `getspell`, `getspelleffects`, `journal`, `menumode`, `removeitem`, `removespell`, `scriptrunning`, `startscript` |
| 2 | Fighters Guild: Alit Trouble in Menaan | TR_m4_Darra (TR_m4_NPC_Darra) | Menaan (-5, -40) | 1 | — | `aiescort`, `aitravel`, `aiwander`, `forcegreeting`, `getaipackagedone`, `getdistance`, `positioncell` |
| 3 | tr_m7_shin_ganettukill | TR_m7_AnatEzharHaddi (TR_m7_Shin_GanEttuKill_sc) | Adammurbael | 2 | — | `getsoundplaying`, `playloopsound3d`, `stopsound` |
| 4 | Thieves Guild: Good Fences, Good Neighbors | TR_m4_Angunas | Bal Foyen, Angunas: Pawnbroker | 1 | — | `choice`, `goodbye`, `moddisposition`, `playsound`, `playsoundvp`, `say`, `setdisposition`, `unlock` |
| 5 | Fighters Guild: Recover the Map of Ushu-Dimmu | TR_m4_Garonag gro-Muk | Mvelthngth-Schel (-26, -41) | 6 | — | `face`, `fall`, `getdeadcount`, `getfatigue`, `getpos`, `modcurrentfatigue`, `setpos`, `startcombat` |
| 6 | Mages Guild: A Rare Enchantment | TR_m2_Ranosa_Orrels | Akamora, Guild of Mages | 3 | — | `addtopic`, `getitemcount`, `getpcrank`, `getpcsneaking`, `modfactionreaction`, `modpcfacrep`, `placeitemcell` |
| 7 | House Telvanni: Uncharted Waters | TR_m1_T_Malvas_Relvani | Port Telvannis, Telvanni Council House: Chambers | 9 | — | `disablelevitation`, `disableteleporting`, `getangle`, `getpccell`, `modreputation`, `pclowerrank`, `placeatme`, `setangle`, `stopscript` |
| 8 | Imperial Legion: Firemoth Rekindled | Galas Drenim | EbonheartVSGrandSCouncilSChambers | 2 | — | `addsoulgem`, `pcraiserank` |
| 9 | Imperial Legion: Firemoth Rekindled | TR_FM_Batshubzub | Firemoth Legion Fort (-16, -21) | 2 | — | `cast`, `explodespell`, `random`, `setatstart` |
| 10 | tr_m7_ns_casino_prisoner | TR_m7_Mirnelea Llothan | Narsis, Sewers: Waterfront Hideout | 2 | — | `getlocked` |
| 11 | Lost in Transit | TR_m4_Yakasamshi | Ernabapalit Camp, Yakasamshi's Yurt | 4 | — | `aiescortcell`, `getfight`, `getinterior`, `getpcsleep`, `setalarm` |
| 12 | House Hlaalu: The Reverse Rescue | TR_m4_q_Galesa Arethi | Bodrum, Varalaryn Tradehouse | 4 | — | `clearforcesneak`, `getdetected`, `removespelleffects` |
| 13 | Mages Guild: Altered Erratum | TR_m7_Malvyn Tinur | Narsis, Foreign Quarter (14, -103) | 4 | — | `modacrobatics`, `placeitem` |
| 14 | Mages Guild: Restored Erratum | TR_m7_Konn | Narsis, Guild of Mages: Arboretum | 5 | — | `getbuttonpressed`, `modalchemy`, `setfatigue` |
| 15 | Shadows Under Aimrah | TR_m3_Ulvo Telvor | Aimrah, Lighthouse | 4 | — | `setscale` |
| 16 | The Slave Whisperer | TR_m4_q_S_dur_juu | AanthirinRegionX8YN40 (8, -40) | 4 | — | `getdisposition`, `getlevel` |
| 17 | Intrigue in Port Telvannis | TR_m1_Q59_MaI_Kaj_slave | Port Telvannis, Irenam Manor | 8 | — | `drop`, `getalchemy`, `getpccrimelevel`, `getrace`, `gotojail`, `lowerrank`, `modpccrimelevel`, `payfine`, `pcclearexpelled`, `pcjoinfaction`, `playloopsound3dvp`, `removeeffects`, `setjournalindex`, `stopcombat` |
| 18 | House Hlaalu: The Twin Lamps | TR_m2_q_27_heelkur | Akamora, Miner's Residence | 6 | — | `getcurrentaipackage`, `position`, `resurrect` |
| 19 | A Smuggler Found | TR_m1_Himnatis | Llothanis, The Water's Shadow Tavern | 6 | — | `aiactivate`, `setdelete` |
| 20 | Born a Necromancer | TR_m7_Belashu Kels | Ussiran Camp, Nind | 7 | — | `getcurrenttime` |
| 21 | Imperial Cult: The Sword of Taldeus | TR_m1_Tarom | Firewatch, Grand Chapel of Akatosh | 8 | — | `move` |
| 22 | The Company We Keep | TR_m3_Relamus_Saravyne | Bosmora, Relamus Saravyne's Quarters | 8 | — | `samefaction` |
| 23 | Imperial Legion: Sabotage | TR_m3_Olfvur Steel-Skin | Ebon Tower, Legion: Headquarters | 8 | — | `getspellreadied` |
| 24 | Flin Galore! | TR_m2_Merro_Galvix | Andar Mok, Andalas Tradehouse | 12 | — | `forcesneak`, `getforcesneak` |
| 25 | The Nameless Dunmer | TR_m2_q_38_favryn | Akamora, The Laughing Goblin | 25 | — | `getlongblade` |
| 26 | The Exiled Duke's Affair | TR_m3_Phyrios Mattimus | Ebon Tower, Palace: High Chambers | 29 | — | `hasitemequipped` |
| 27 | House Hlaalu: Money Never Sleeps | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 10 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 28 | House Hlaalu: Dealing with Orvas Dren | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 3 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 29 | House Hlaalu: Expel the Outlanders | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 3 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 30 | House Hlaalu: Hlaalu Grandmaster | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 2 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 31 | House Hlaalu: Finding the Saint | TR_m7_FatFuckFrank | Narsis, Second Family Manor: Grandmaster Quarters | 9 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 32 | House Hlaalu: The Ritual of St. Veloth | TR_m7_FatFuckFrank | Narsis, Second Family Manor: Grandmaster Quarters | 5 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | `geteffect` |
| 33 | House Hlaalu: The Face of Veloth | TR_m7_FatFuckFrank | Narsis, Second Family Manor: Grandmaster Quarters | 4 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | `getsquareroot`, `saydone` |
| 34 | Temple: Pilgrimage of the Saint's Rescue | TR_m7_HH_ogrim_ritual | Kalkusara, Shrine | 2 | — | — |
| 35 | Blood Ties | dhaunayne aundae | Ashmelech | 15 | unlocks *A Broken Family* | — |
| 36 | The Vampire Hunter | dhaunayne aundae | Ashmelech | 11 | unlocks *A Broken Family* | — |
| 37 | The Vampire Merta | raxle berne | GalomSDaeusVSObservatory | 7 | unlocks *A Broken Family* | — |
| 38 | The Quarra Amulet | volrina quarra | DruscashtiVSLowerSLevel | 6 | unlocks *A Broken Family* | — |
| 39 | A Broken Family | TR_m2_Velyn Alari | Sadalvel Ancestral Tomb | 19 | — | `modalarm` |
| 40 | House Hlaalu: Crackdown | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 2 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 41 | Thieves Guild: Flying Too Close | TR_m7_Cervo Cantaber | Shipal-Sharai, Cervo Cantaber: Master Arbalest | 3 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 42 | Thieves Guild: To the Limit | TR_m7_Thorleif | Narsis, Redwater Theater | 4 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 43 | Thieves Guild: Before the Dust Settles | TR_m7_Thorleif | Narsis, Redwater Theater | 4 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 44 | Thieves Guild: Flying Too Close | TR_m7_Thorleif | Narsis, Redwater Theater | 5 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 45 | Thieves Guild: Born to Be Wild | TR_m7_Thorleif | Narsis, Redwater Theater | 4 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 46 | Thieves Guild: The Devil You Know | TR_m7_Thorleif | Narsis, Redwater Theater | 3 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 47 | Thieves Guild: Friends in Low Places | TR_m7_Thorleif | Narsis, Redwater Theater | 10 | unlocks *Ja-Natta Syndicate: Consolidation* | `lock` |
| 48 | Thieves Guild: Offer They Can't Refuse | TR_m7_Thorleif | Narsis, Redwater Theater | 3 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 49 | Thieves Guild: Show Me Your Moves | TR_m7_Thorleif | Narsis, Redwater Theater | 5 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 50 | Thieves Guild: Take the House | TR_m7_Thorleif | Narsis, Redwater Theater | 3 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 51 | Ja-Natta Syndicate: Snake Eyes | TR_m7_JNS_TheBoss | Uddanu, Chamber of K'Vatra | 14 | unlocks *Ja-Natta Syndicate: Consolidation* | `hassoulgem`, `pcexpelled`, `removesoulgem` |
| 52 | Ja-Natta Syndicate: End of the Line | TR_m7_JNS_TheBoss | Uddanu, Chamber of K'Vatra | 7 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 53 | Ja-Natta Syndicate: Consolidation | TR_m7_JNS_TheBoss | Uddanu, Chamber of K'Vatra | 3 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 54 | Ja-Natta Syndicate: Breathing Out | TR_m7_JNS_TheBoss | Uddanu, Chamber of K'Vatra | 4 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 55 | Ja-Natta Syndicate: Breathing In | TR_m7_JNS_TheBoss | Uddanu, Chamber of K'Vatra | 6 | unlocks *Ja-Natta Syndicate: Consolidation* | — |
| 56 | Ja-Natta Syndicate: Consolidation | TR_m7_JNS_TheBoss | Uddanu, Chamber of K'Vatra | 3 | — | — |

**226 further command(s) share a handler with one of these and are covered by testing it** -- `Enable` and `Disable` are one `OpSetEnabled`, every attribute and skill command one `OpStat`. The classes come from the runtime's own registrations, and `OpStat` is split by verb and by whether the stat maps to a Skyrim actor value, since those are genuinely different code paths.

| Tested | Also covers |
|---|---|
| `modalchemy` | `modalteration`, `modarmorbonus`, `modarmorer`, `modblock`, `modconjuration`, `moddestruction`, `modenchant`, `modhandtohand`, `modheavyarmor`, `modillusion`, `modinvisible`, `modlightarmor`, `modmarksman`, `modmercantile`, `modmysticism`, `modparalysis`, `modresistdisease`, `modresistfire`, `modresistfrost`, `modresistmagicka`, `modresistpoison`, `modresistshock`, `modrestoration`, `modsecurity`, `modshortblade`, `modsneak`, `modspear`, `modspeechcraft`, `modunarmored`, `modwaterbreathing`, `modwaterwalking`, `setalchemy`, `setalteration`, `setarmorbonus`, `setarmorer`, `setblock`, `setconjuration`, `setdestruction`, `setenchant`, `sethandtohand`, `setheavyarmor`, `setillusion`, `setinvisible`, `setlightarmor`, `setmarksman`, `setmercantile`, `setmysticism`, `setparalysis`, `setresistdisease`, `setresistfire`, `setresistfrost`, `setresistmagicka`, `setresistpoison`, `setresistshock`, `setrestoration`, `setsecurity`, `setshortblade`, `setsneak`, `setspear`, `setspeechcraft`, `setunarmored`, `setwaterbreathing`, `setwaterwalking` |
| `modacrobatics` | `modagility`, `modathletics`, `modattackbonus`, `modblindness`, `modcastpenalty`, `modchameleon`, `moddefendbonus`, `modendurance`, `modflying`, `modintelligence`, `modluck`, `modpersonality`, `modresistblight`, `modresistcorprus`, `modresistnormalweapons`, `modresistparalysis`, `modsilence`, `modspeed`, `modstrength`, `modsuperjump`, `modswimspeed`, `modwillpower`, `setacrobatics`, `setagility`, `setathletics`, `setattackbonus`, `setblindness`, `setcastpenalty`, `setchameleon`, `setdefendbonus`, `setendurance`, `setflying`, `setintelligence`, `setluck`, `setpersonality`, `setresistblight`, `setresistcorprus`, `setresistnormalweapons`, `setresistparalysis`, `setsilence`, `setspeed`, `setstrength`, `setsuperjump`, `setswimspeed`, `setwillpower` |
| `getalchemy` | `getalteration`, `getarmorbonus`, `getarmorer`, `getblock`, `getconjuration`, `getdestruction`, `getenchant`, `gethandtohand`, `getheavyarmor`, `getillusion`, `getinvisible`, `getlightarmor`, `getmarksman`, `getmercantile`, `getmysticism`, `getparalysis`, `getresistdisease`, `getresistfire`, `getresistfrost`, `getresistmagicka`, `getresistpoison`, `getresistshock`, `getrestoration`, `getsecurity`, `getshortblade`, `getsneak`, `getspear`, `getspeechcraft`, `getunarmored`, `getwaterbreathing`, `getwaterwalking` |
| `getagility` | `getacrobatics`, `getathletics`, `getattackbonus`, `getblindness`, `getcastpenalty`, `getchameleon`, `getdefendbonus`, `getendurance`, `getflying`, `getintelligence`, `getluck`, `getpersonality`, `getresistblight`, `getresistcorprus`, `getresistnormalweapons`, `getresistparalysis`, `getsilence`, `getspeed`, `getstrength`, `getsuperjump`, `getswimspeed`, `getwillpower` |
| `getlongblade` | `getaxe`, `getbluntweapon`, `getmediumarmor`, `modaxe`, `modbluntweapon`, `modlongblade`, `modmediumarmor`, `setaxe`, `setbluntweapon`, `setlongblade`, `setmediumarmor` |
| `disable` | `disableplayercontrols`, `disableplayerfighting`, `disableplayerlooking`, `disableplayerviewswitch`, `enable`, `enableplayercontrols`, `enableplayerfighting`, `enableplayerlooking`, `enableplayerviewswitch` |
| `modcurrentfatigue` | `modcurrenthealth`, `modcurrentmagicka`, `modfatigue`, `modhealth`, `modmagicka` |
| `getdisabled` | `getplayercontrolsdisabled`, `getplayerfightingdisabled`, `getplayerlookingdisabled`, `getplayerviewswitchdisabled` |
| `getdetected` | `getlineofsight`, `getlos`, `gettarget` |
| `getfight` | `getalarm`, `getflee`, `gethello` |
| `modalarm` | `modfight`, `modflee`, `modhello` |
| `setalarm` | `setfight`, `setflee`, `sethello` |
| `cellchanged` | `onactivate`, `ondeath` |
| `getfatigue` | `gethealth`, `getmagicka` |
| `setfatigue` | `sethealth`, `setmagicka` |
| `aiescort` | `aifollow` |
| `aiescortcell` | `aifollowcell` |
| `disablelevitation` | `enablelevitation` |
| `disableteleporting` | `enableteleporting` |
| `getpcsneaking` | `getpcrunning` |
| `getspellreadied` | `getweapondrawn` |
| `getstartingangle` | `getstartingpos` |
| `lowerrank` | `raiserank` |
| `modpccrimelevel` | `setpccrimelevel` |
| `modpcfacrep` | `setpcfacrep` |
| `move` | `moveworld` |
| `payfine` | `payfinethief` |
| `pcclearexpelled` | `pcexpell` |
| `placeatme` | `placeatpc` |
| `playloopsound3d` | `playsound3d` |
| `playloopsound3dvp` | `playsound3dvp` |
| `ra` | `resetactors` |
| `rotate` | `rotateworld` |

**Called by Morrowind.esm + TR_Mainland.esm, but never from a quest** — these sit on ambient object scripts (doors, cranks, lights), so they need a hand-written probe rather than a quest:

`rotate` (262), `getscale` (15), `showrestmenu` (11), `getstartingangle` (7), `modscale` (4), `getagility` (2), `getreputation` (1), `enableracemenu` (1)

**No call site anywhere in Morrowind.esm + TR_Mainland.esm** (6) — untestable from it at all; they need a different plugin:

`dropsoulgem`, `getfactionreaction`, `getpcfacrep`, `ra`, `setfactionreaction`, `setreputation`

## Stubbed commands — registered but doing nothing (18 of 40 covered)

Running these routes the player through commands that compile and dispatch but do nothing, so the log names the silent no-op behind each broken stage.

| # | Quest | Quest giver | `coc` target / location | Stages | Why | Commands it is first to test |
|--:|---|---|---|--:|---|---|
| 1 | Temple: Epidemic in Ranyon-ruhn | TR_m1_Arthal_Eindari (TR_m1_q_TT_5_VampNPCA) | Ranyon-ruhn, Arthal Eindari's House | 1 | — | `getcommondisease` |
| 2 | The Citadels of the Sixth House | dagoth_ur_1 | ? | 4 | unlocks *Sleepers Awake* | `changeweather`, `modregion`, `playbink`, `playgroup` |
| 3 | Sleepers Awake | Alvura Othrenim | VivecVSArenaSWaistworks | 1 | — | — |
| 4 | Temple: Armun Ashlands Avenger | TR_m4_D_KhirakaiBandit1 (TR_m4_TT_ArmunAdvC_sc) | Khirakai | 2 | — | `getwindspeed` |
| 5 | House Telvanni: Mudan-Mul Egg Mine | aryon | TelSVosVSAryonAsSChambers | 4 | — | `getblightdisease` |
| 6 | Lost in Transit | TR_m4_Yakasamshi | Ernabapalit Camp, Yakasamshi's Yurt | 4 | — | `getarmortype` |
| 7 | House Telvanni: Fools That Meddle | TR_m1_T_Norahin_Darys | Port Telvannis, Telvanni Council House: Chambers | 6 | — | `onmurder` |
| 8 | Dead Shores | TR_m3_Reynard Valtienne | Wavebreaker Keep, Great Hall | 6 | — | `getattacked` |
| 9 | House Hlaalu: Omaynis Inn | TR_m4_q_Ervan Indrano | Omaynis (-14, -31) | 6 | — | `onknockout` |
| 10 | Mages Guild: Enchanted Erratum | TR_m7_Anatolius Datus | Narsis, Guild of Mages: Genatorium | 6 | — | `hurtcollidingactor` |
| 11 | Kill or Be Killed | TR_m1_Medenb_Khifzah | Tel Ouada, Midaan Manor | 10 | — | `wakeuppc` |
| 12 | The Rift | TR_m3_Ralam_Othravel | AltOrethanRegionX28YN64 (28, -64) | 18 | — | `modwaterlevel` |
| 13 | House Hlaalu: Money Never Sleeps | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 10 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 14 | House Hlaalu: Dealing with Orvas Dren | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 3 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 15 | House Hlaalu: Expel the Outlanders | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 3 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 16 | House Hlaalu: Hlaalu Grandmaster | TR_m7_Ereven Peronys | Narsis, Measurehall: Vizier's Tower | 2 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 17 | House Hlaalu: Finding the Saint | TR_m7_FatFuckFrank | Narsis, Second Family Manor: Grandmaster Quarters | 9 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | — |
| 18 | House Hlaalu: The Ritual of St. Veloth | TR_m7_FatFuckFrank | Narsis, Second Family Manor: Grandmaster Quarters | 5 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | `disableplayerjumping` |
| 19 | House Hlaalu: The Face of Veloth | TR_m7_FatFuckFrank | Narsis, Second Family Manor: Grandmaster Quarters | 4 | unlocks *Temple: Pilgrimage of the Saint's Rescue* | `loopgroup` |
| 20 | Temple: Pilgrimage of the Saint's Rescue | TR_m7_HH_ogrim_ritual | Kalkusara, Shrine | 2 | — | — |
| 21 | The Nameless Dunmer | TR_m2_q_38_favryn | Akamora, The Laughing Goblin | 25 | — | `streammusic` |
| 22 | Fighters Guild: Belated Service | TR_m2_Amiro | Akamora, Guild of Fighters | 8 | unlocks *Fighters Guild: Belated Service* | — |
| 23 | Walk the Talk | TR_m2_Hozgub gro-Hazor | The Inn Between | 30 | unlocks *Fighters Guild: Belated Service* | `getweapontype` |
| 24 | Fighters Guild: Belated Service | TR_m2_Amiro | Akamora, Guild of Fighters | 8 | — | — |

**Called by Morrowind.esm + TR_Mainland.esm, but never from a quest** — these sit on ambient object scripts (doors, cranks, lights), so they need a hand-written probe rather than a quest:

`getstandingpc` (19), `getwaterlevel` (15), `getstandingactor` (14), `xbox` (13), `removefromlevcreature` (13), `setwaterlevel` (12), `getpcjumping` (11), `getcollidingpc` (9), `hurtstandingactor` (6), `togglemenus` (4), `skipanim` (3), `menutest` (3), `hitonme` (3), `enablestatsmenu` (3), `enablemapmenu` (3), `enablemagicmenu` (3), `enableinventorymenu` (3), `enablestatreviewmenu` (1), `enablerest` (1), `enablenamemenu` (1), `enableclassmenu` (1), `enablebirthmenu` (1)

