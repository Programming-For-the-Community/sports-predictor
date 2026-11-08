import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/legacy.dart';

import '../../core/auth/auth_repository.dart';
import '../../core/theme/app_colors.dart';
import 'assistant_controller.dart';
import 'assistant_header.dart';
import 'assistant_input.dart';
import 'assistant_messages.dart';
import 'assistant_models.dart';

/// Which agent the panel talks to. Everyone uses Predictions; admins can
/// switch to Infrastructure.
final assistantModeProvider = StateProvider<AssistantMode>((ref) => AssistantMode.predictions);

/// The whole assistant: used as the phone's bottom sheet and as the web's
/// docked side panel (design: the approved assistant UI mock-ups).
class AssistantPanel extends ConsumerStatefulWidget {
  const AssistantPanel({super.key, this.context = const AssistantContext(), this.onClose});

  final AssistantContext context;
  final VoidCallback? onClose;

  @override
  ConsumerState<AssistantPanel> createState() => _AssistantPanelState();
}

class _AssistantPanelState extends ConsumerState<AssistantPanel> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _loadStatus(ref.read(assistantModeProvider)));
  }

  void _loadStatus(AssistantMode mode) {
    if (ref.read(assistantControllerProvider(mode)).questionsLeft == null) {
      ref.read(assistantControllerProvider(mode).notifier).loadStatus();
    }
  }

  List<String> _suggestions(AssistantMode mode) {
    if (mode == AssistantMode.infrastructure) {
      return const ['Spend so far this month?', 'Any serious repeat offenders this week?', 'Which endpoints are being probed?'];
    }
    return [
      'How accurate is this model?',
      'Biggest upsets this week',
      if (widget.context.eventId != null) 'Player props for this game',
    ];
  }

  @override
  Widget build(BuildContext context) {
    final isAdmin = ref.watch(isAdminProvider);
    final mode = isAdmin ? ref.watch(assistantModeProvider) : AssistantMode.predictions;
    final state = ref.watch(assistantControllerProvider(mode));
    final controller = ref.read(assistantControllerProvider(mode).notifier);
    void ask(String text) => controller.ask(text, context: widget.context);

    return Material(
      color: AppColors.menu,
      child: Column(
        children: [
          AssistantHeader(
            mode: mode,
            isAdmin: isAdmin,
            favorites: state.favorites,
            onClose: widget.onClose,
            onNewChat: controller.newChat,
            onRemoveFavorite: controller.removeFavorite,
            onModeChanged: (next) {
              ref.read(assistantModeProvider.notifier).state = next;
              _loadStatus(next);
            },
          ),
          Expanded(
            child: AssistantMessages(
              messages: state.messages,
              busy: state.busy,
              progressTool: state.progressTool,
              suggestions: _suggestions(mode),
              onSuggestion: ask,
              contextLabel: widget.context.eventLabel,
            ),
          ),
          AssistantInput(
            hint: mode == AssistantMode.predictions ? 'Ask about a game or model' : 'Ask about cost or traffic',
            enabled: !state.busy,
            quotaReached: state.quotaReached,
            questionsLeft: state.questionsLeft,
            dailyLimit: state.dailyLimit,
            isAdmin: mode == AssistantMode.infrastructure,
            onSend: ask,
          ),
        ],
      ),
    );
  }
}
