ScriptName TES4_Chargen Hidden
{Asks MorrowindRuntime for its class or birthsign menu (kind 1 or 2) through
the plugin's TES4ChargenRequest global. The runtime takes the request by
writing minus the kind, opens the one menu every game shares, grants the
pick's spells, writes the plugin's own index of the pick + 1 into the choice
global (-1 when the plugin lists no such entry) and sets the request back to
0. Nobody taking it within a second means no runtime menu: the caller shows
its own message pages instead.
See: docs/commentary/morrowind_runtime.md#chargen-menus}

; The plugin's index of the pick; -2 for a pick it does not list, whose
; choice global is cleared; -1 when the runtime did not take the request.
Int Function Ask(GlobalVariable request, Int kind, GlobalVariable choice) Global
  If !request || !choice
    Return -1
  EndIf
  request.SetValue(kind)
  Int waited = 0
  While request.GetValue() == kind && waited < 10
    Utility.WaitMenuMode(0.1)
    waited += 1
  EndWhile
  If request.GetValue() == kind
    request.SetValue(0)
    Return -1
  EndIf
  While request.GetValue() != 0
    Utility.WaitMenuMode(0.25)
  EndWhile
  Int picked = choice.GetValue() as Int
  If picked < 0
    choice.SetValue(0)
    Return -2
  EndIf
  Return picked - 1
EndFunction
