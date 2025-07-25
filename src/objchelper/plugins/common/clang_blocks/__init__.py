__all__ = ["clang_block_args_analyzer_component", "clang_block_optimizer_component"]

import ida_kernwin
import idaapi

from objchelper.base.reloadable_plugin import HexraysHookComponent, UIAction, UIActionsComponent
from objchelper.idahelper import widgets
from objchelper.idahelper.ast import cfunc

from .analyze_byref_args import try_add_block_arg_byref_to_func
from .optimize_blocks_init import objc_blocks_optimizer_hooks_t
from .utils import run_objc_plugin_on_func

ACTION_ID = "objchelper:restore_llvm_block_args_byref"

clang_block_args_analyzer_component = UIActionsComponent.factory(
    "Clang Blocks - __block arguments",
    [
        lambda core: UIAction(
            ACTION_ID,
            idaapi.action_desc_t(
                ACTION_ID,
                "Analyze stack-allocated blocks and their __block args (current function)",
                ClangBlockDetectByrefAction(),
                "Alt+Shift+s",
            ),
            menu_location=UIAction.base_location(core),
        )
    ],
)

clang_block_optimizer_component = HexraysHookComponent.factory(
    "Clang Blocks - optimizer", [objc_blocks_optimizer_hooks_t]
)


class ClangBlockDetectByrefAction(ida_kernwin.action_handler_t):
    def activate(self, ctx: ida_kernwin.action_ctx_base_t):
        if ctx.cur_func is None:
            print("No function selected")
            return 0

        run_objc_plugin_on_func(ctx.cur_ea)
        widgets.refresh_pseudocode_widgets()
        decompiled = cfunc.from_ea(ctx.cur_ea)
        if decompiled is None:
            print(f"Failed to decompile func at {ctx.cur_ea:X}")
            return

        try_add_block_arg_byref_to_func(decompiled)
        return 0

    def update(self, ctx) -> int:
        return idaapi.AST_ENABLE_ALWAYS
