// `PlayGroup`, `LoopGroup` and `SkipAnim`: the object's own animation groups, which the
// mesh conversion ships as named sequences. The queueing itself is the game
// side's (game_calls_anim.cpp); these only read the arguments the way
// OpenMW's animationextensions.cpp does and hand them over.

#include <string>

#include <components/compiler/opcodes.hpp>
#include <components/interpreter/context.hpp>
#include <components/interpreter/opcodes.hpp>

#include "dialogue_state.h"
#include "log.h"
#include "script_ops.h"

namespace tesruntime::mw {

namespace {

// TES3's modes: 0 queue behind what plays, 1 start now, 2 start now at the
// loop start. Anything else is a script error OpenMW throws on.
constexpr int kLastMode = 2;

int PopMode(Interpreter::Runtime& runtime, unsigned int optional) {
    const int mode = optional > 0 ? PopInt(runtime) : 0;
    return mode < 0 || mode > kLastMode ? 0 : mode;
}

void Queue(const std::string& ref, const std::string& group, int mode,
           std::uint32_t loops) {
    if (Hooks().isDisabled && Hooks().isDisabled(ref)) return;
    LogVerbose("anim: %s %s mode %d loops %u", ref.c_str(), group.c_str(), mode,
               loops);
    if (Hooks().playGroup) Hooks().playGroup(ref, group, mode, loops);
}

// `PlayGroup group [mode]`: a looping group repeats until replaced.
template <class R>
class OpPlayAnim : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string ref = R::Target(runtime);
        const std::string group = PopString(runtime);
        const int mode = PopMode(runtime, optional);
        Queue(ref, group, mode, kLoopForever);
    }
};

// `LoopGroup group count [mode]`: the group repeats `count` more times.
template <class R>
class OpLoopAnim : public Interpreter::Opcode1 {
    void execute(Interpreter::Runtime& runtime, unsigned int optional) override {
        const std::string ref = R::Target(runtime);
        const std::string group = PopString(runtime);
        const int loops = PopInt(runtime);
        const int mode = PopMode(runtime, optional);
        Queue(ref, group, mode, loops > 0 ? static_cast<std::uint32_t>(loops) : 0u);
    }
};

// `SkipAnim`: the object's animation does not advance this frame.
template <class R>
class OpSkipAnim : public Interpreter::Opcode0 {
    void execute(Interpreter::Runtime& runtime) override {
        const std::string ref = R::Target(runtime);
        if (Hooks().skipAnim) Hooks().skipAnim(ref);
    }
};

}  // namespace

void InstallAnimOps(OpcodeInstaller& into) {
    namespace A = Compiler::Animation;
    InstallPair<OpSkipAnim>(into, A::opcodeSkipAnim, A::opcodeSkipAnimExplicit);
    into.Real3<OpPlayAnim<Implicit>>(A::opcodePlayAnim);
    into.Real3<OpPlayAnim<Explicit>>(A::opcodePlayAnimExplicit);
    into.Real3<OpLoopAnim<Implicit>>(A::opcodeLoopAnim);
    into.Real3<OpLoopAnim<Explicit>>(A::opcodeLoopAnimExplicit);
}

}  // namespace tesruntime::mw
