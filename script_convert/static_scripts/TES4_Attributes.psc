ScriptName TES4_Attributes Hidden
{The TES4 attributes and kept skills converted scripts read and write.
The player's eight attributes are one TES4Player<Attribute> global each:
MorrowindRuntime writes the attribute into its global and turns a script's
write into a change of the attribute. A global no plugin supplies reads as the
old stub, 100, so a gate on it falls open. Every other actor's attributes and
kept skills are its rank in a hidden stat faction, joined at its authored
value; the player's kept skills read the Skyrim value they stand in for.
See: docs/commentary/morrowind_runtime.md#npc-attributes}

Float Function Read(GlobalVariable attribute) Global
  If attribute
    Return attribute.GetValue()
  EndIf
  Return 100.0
EndFunction

Function Write(GlobalVariable attribute, Float value) Global
  If attribute
    attribute.SetValue(value)
  EndIf
EndFunction

Function Modify(GlobalVariable attribute, Float amount) Global
  If attribute
    attribute.SetValue(attribute.GetValue() + amount)
  EndIf
EndFunction

; The actor's rank in stat, or -1 when it holds none.
Int Function Rank(Actor who, Faction stat) Global
  If who && stat
    Int rank = who.GetFactionRank(stat)
    If rank >= 0
      Return rank
    EndIf
  EndIf
  Return -1
EndFunction

; Holds value as the actor's rank in stat, which joins it; a rank is a signed byte.
Function SetRank(Actor who, Faction stat, Float value) Global
  If !who || !stat
    Return
  EndIf
  Int rank = value as Int
  If rank < 0
    rank = 0
  ElseIf rank > 127
    rank = 127
  EndIf
  who.SetFactionRank(stat, rank)
EndFunction

Bool Function IsPlayer(Actor who) Global
  Return who && who == Game.GetPlayer()
EndFunction

Float Function ReadActor(Actor who, Faction stat, GlobalVariable playerValue) Global
  If IsPlayer(who)
    Return Read(playerValue)
  EndIf
  Int rank = Rank(who, stat)
  If rank >= 0
    Return rank as Float
  EndIf
  Return 100.0
EndFunction

Function WriteActor(Actor who, Faction stat, GlobalVariable playerValue, Float value) Global
  If IsPlayer(who)
    Write(playerValue, value)
  Else
    SetRank(who, stat, value)
  EndIf
EndFunction

Function ModifyActor(Actor who, Faction stat, GlobalVariable playerValue, Float amount) Global
  WriteActor(who, stat, playerValue, ReadActor(who, stat, playerValue) + amount)
EndFunction

Float Function ReadSkill(Actor who, Faction stat, String playerValue) Global
  Int rank = -1
  If !IsPlayer(who)
    rank = Rank(who, stat)
  EndIf
  If rank >= 0
    Return rank as Float
  ElseIf who
    Return who.GetActorValue(playerValue)
  EndIf
  Return 0.0
EndFunction

Function WriteSkill(Actor who, Faction stat, String playerValue, Float value) Global
  If IsPlayer(who)
    who.SetActorValue(playerValue, value)
  Else
    SetRank(who, stat, value)
  EndIf
EndFunction

Function ModifySkill(Actor who, Faction stat, String playerValue, Float amount) Global
  WriteSkill(who, stat, playerValue, ReadSkill(who, stat, playerValue) + amount)
EndFunction
