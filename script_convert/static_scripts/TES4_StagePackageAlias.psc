ScriptName TES4_StagePackageAlias extends ReferenceAlias Hidden
{A converted quest alias carrying the quest's AI packages. Oblivion re-checked
an actor's packages on its own once a stage it was gated on was set; Skyrim
only does on EvaluatePackage. TES4Polyfill.StageSet sends this quest's event
after every converted SetStage, and each alias re-checks its own actor on its
own thread, so the caller never waits.
See docs/commentary/script_convert.md#setstage-re-evaluates-alias-packages}

Event OnInit()
  RegisterForModEvent(TES4Polyfill.StageEventName(GetOwningQuest()), "OnTES4StageSet")
EndEvent

Event OnTES4StageSet(String asEventName, String asArg, Float afArg, Form akSender)
  Actor who = GetActorReference()
  If who
    who.EvaluatePackage()
  EndIf
EndEvent
