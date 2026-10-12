ScriptName TESGameSelectMQ101 extends Quest Hidden
{Threads of Prophecy — the MQ101 takeover.

Attached to the VANILLA MQ101 record (0003372B), which this plugin overrides.
The build RETARGETS two fragments; nothing else about the record changes:

  stage 0 / entry 0  QF_MQ101_0003372B.Fragment_2 (gated MQQuickstart == 0, the
                     real new-game path) -> RunTakeover
  stage 10 / entry 0 QF_MQ101_0003372B.Fragment_4 (the opening itself)
                     -> RunOpening, which runs Fragment_4 with the player's
                     items set aside so its RemoveAllItems takes nothing

See docs/commentary/tesgameselect.md#mq101-takeover}

; The selector quest that owns the prompt and the per-game handoff.
TESGameSelectQuest Property Selector Auto

; Skyrim's own empty interior cell marker (WIDeadBodyCleanupCellMarker) — the
; player waits here while the prompt is up, somewhere genuinely blank.
ObjectReference Property HoldingCellMarker Auto

; Skyrim.esm's GameHour global (0x38). Choosing Skyrim replays vanilla
; Fragment_2 verbatim, and its first line is GameHour.SetValue(7).
GlobalVariable Property GameHour Auto

; An empty, non-respawning vanilla chest base (TreasChestSmallEMPTYNoRespawn)
; that holds the player's items while vanilla stage 10 strips the player.
Container Property StashChest Auto

; The travel quest, told which game the new game began with.
TESGameSelectTravel Property Travel Auto

; ---------------------------------------------------------------------------
; Fired from MQ101 stage 0, log entry 0 — the retargeted vanilla fragment.
; Runs once per new game; debug quickstarts bypass it on their own entries.
; ---------------------------------------------------------------------------
Function RunTakeover()
  If Selector == None
    Debug.Trace("[TESGameSelect] selector quest unbound; vanilla start proceeds")
    VanillaStart()
    Return
  EndIf
  If Selector.HasRun
    Return
  EndIf

  ; Freeze the (not yet started) opening: no controls, no saving.
  Game.DisablePlayerControls()
  Game.SetInChargen(true, true, false)

  ParkPlayer(Game.GetPlayer())

  Selector.RunSelection()
  Selector.HoldOpeningsFor(Selector.ChosenGame)

  ; A new game's player carries Skyrim's player base record items (iron
  ; armor, potions, gold...), which only a main-menu `coc` is meant to keep;
  ; vanilla strips them at stage 10, long after the player has loaded and put
  ; them on. Strip them here, after the load and the prompt, whichever game is
  ; chosen, so the stage-10 wrapper has nothing of them to hand back. Quest
  ; items (the Elder Scroll) are kept by RemoveAllItems.
  Game.GetPlayer().RemoveAllItems()

  ; Hand the engine back its default state before either path starts.
  Game.SetInChargen(false, false, false)
  Game.EnablePlayerControls()

  If !Selector.ChoseSkyrim()
    Selector.BeginChosenGame()
  EndIf
  ; Otherwise the other game owns the player now. MQ101 stays RUNNING at stage
  ; 0 — no cart, no Helgen, nothing downstream — until the scroll begins
  ; Skyrim. 🛑 Never Stop() it: MQ101 is Run Once (DNAM 0x0100), so a stopped
  ; MQ101 can never be started again and Skyrim could never be begun.
  If Selector.ChoseSkyrim()
    VanillaStart()
  EndIf
  Selector.Selecting = false
  If Travel != None
    Travel.Arrive(Selector.ChosenGame)
  EndIf
EndFunction

; Hold the player in the holding cell until the prompt may show: loaded there,
; and left there for ParkSettleTicks in a row. Another game's Start-Game-Enabled
; opening starts AFTER this fragment (Nehrim's Charactergen moves the player
; half a second later), so every tick holds the openings again and brings the
; player back. The settle also waits out the initial load: a Message.Show()
; issued during it is drawn twice. Utility.Wait only elapses while the game is
; unpaused. Capped so a pathological load can never wedge the takeover.
; See docs/commentary/tesgameselect.md#opening-hold
Int Property ParkSettleTicks = 10 AutoReadOnly

Function ParkPlayer(Actor player)
  Cell holding = None
  If HoldingCellMarker != None
    holding = HoldingCellMarker.GetParentCell()
  EndIf
  Int settled = 0
  Int guard = 0
  While settled < ParkSettleTicks && guard < 300
    Selector.HoldOpeningMovers()
    If holding != None && player.GetParentCell() != holding
      Debug.Trace("[TESGameSelect] player left the holding cell; bringing them back")
      player.MoveTo(HoldingCellMarker)
      settled = 0
    ElseIf player.Is3DLoaded()
      settled += 1
    EndIf
    Utility.Wait(0.1)
    guard += 1
  EndWhile
EndFunction

; Vanilla QF_MQ101_0003372B.Fragment_2, verbatim: the whole normal-start
; fragment is these two lines, and stage 10 does everything else.
Function VanillaStart()
  If GameHour != None
    GameHour.SetValue(7)
  EndIf
  SetStage(10)
EndFunction

; The travel scroll beginning Skyrim after another game: MQ101 is still
; waiting at stage 0, so replay the normal start. False when it is not
; running and will not start — a save from a build that stopped it, which Run
; Once makes permanent.
Bool Function BeginSkyrim()
  If !IsRunning() && !Start()
    Debug.Trace("[TESGameSelect] MQ101 was stopped and is Run Once; Skyrim cannot begin")
    Return false
  EndIf
  VanillaStart()
  Return true
EndFunction

; ---------------------------------------------------------------------------
; Fired from MQ101 stage 10, log entry 0, in place of vanilla Fragment_4.
; Fragment_4 opens with RemoveAllItems on the player, meant only for the
; player base record's starting items, which the takeover has already
; stripped on a new game; a player arriving from another game must keep what
; they carry. Items come back unequipped; quest items never leave
; (RemoveAllItems keeps them by default).
; ---------------------------------------------------------------------------
Function RunOpening()
  Actor player = Game.GetPlayer()
  ObjectReference stash = None
  If StashChest != None && HoldingCellMarker != None
    stash = HoldingCellMarker.PlaceAtMe(StashChest, 1, true)
    player.RemoveAllItems(stash, true)
  EndIf

  ((Self as Quest) as QF_MQ101_0003372B).Fragment_4()

  If stash != None
    stash.RemoveAllItems(player, true)
    stash.Delete()
  EndIf
EndFunction
