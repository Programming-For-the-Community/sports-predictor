import 'package:flutter/material.dart';

import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import 'assistant_models.dart';

/// Title row, the admins-only Predictions | Infrastructure switch, and the
/// saved-favourites row (Predictions mode only).
class AssistantHeader extends StatelessWidget {
  const AssistantHeader({
    super.key,
    required this.mode,
    required this.isAdmin,
    required this.favorites,
    required this.onModeChanged,
    required this.onRemoveFavorite,
    required this.onNewChat,
    this.onClose,
  });

  final AssistantMode mode;
  final bool isAdmin;
  final Favorites favorites;
  final ValueChanged<AssistantMode> onModeChanged;
  final void Function(String sport, String team) onRemoveFavorite;
  final VoidCallback onNewChat;
  final VoidCallback? onClose;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 6, 8, 6),
          child: Row(
            children: [
              Expanded(
                child: Text(isAdmin ? 'Assistant' : 'Ask about predictions', style: AppTextStyles.sectionTitle()),
              ),
              PopupMenuButton<void>(
                tooltip: 'Assistant menu',
                icon: const Icon(Icons.more_horiz, color: AppColors.inkSub),
                color: AppColors.menu,
                itemBuilder: (context) => [PopupMenuItem<void>(onTap: onNewChat, child: Text('New chat', style: AppTextStyles.body()))],
              ),
              if (onClose != null)
                IconButton(tooltip: 'Close assistant', onPressed: onClose, icon: const Icon(Icons.close, color: AppColors.inkSub)),
            ],
          ),
        ),
        if (isAdmin) _ModeSwitch(mode: mode, onChanged: onModeChanged),
        if (mode == AssistantMode.predictions && favorites.isNotEmpty) _FavoritesRow(favorites: favorites, onRemove: onRemoveFavorite),
        const Divider(height: 1, color: AppColors.border),
      ],
    );
  }
}

class _ModeSwitch extends StatelessWidget {
  const _ModeSwitch({required this.mode, required this.onChanged});
  final AssistantMode mode;
  final ValueChanged<AssistantMode> onChanged;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
      child: Container(
        padding: const EdgeInsets.all(4),
        decoration: BoxDecoration(color: AppColors.surface, borderRadius: BorderRadius.circular(999)),
        child: Row(
          children: [
            for (final option in AssistantMode.values)
              Expanded(
                child: Semantics(
                  selected: option == mode,
                  button: true,
                  child: InkWell(
                    borderRadius: BorderRadius.circular(999),
                    onTap: () => onChanged(option),
                    child: Container(
                      height: 36,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(
                        color: option == mode ? AppColors.cyan : Colors.transparent,
                        borderRadius: BorderRadius.circular(999),
                      ),
                      child: Text(
                        option.label,
                        style: AppTextStyles.body(color: option == mode ? AppColors.bg : AppColors.inkSub).copyWith(fontSize: 13),
                      ),
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _FavoritesRow extends StatelessWidget {
  const _FavoritesRow({required this.favorites, required this.onRemove});
  final Favorites favorites;
  final void Function(String sport, String team) onRemove;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
      child: Wrap(
        spacing: 8,
        runSpacing: 8,
        crossAxisAlignment: WrapCrossAlignment.center,
        children: [
          Text('YOUR TEAMS', style: AppTextStyles.microLabel(color: AppColors.inkSub)),
          for (final entry in favorites.entries)
            for (final team in entry.value)
              InputChip(
                label: Text('${entry.key.toUpperCase()} · $team', style: AppTextStyles.microLabel(color: AppColors.ink)),
                onDeleted: () => onRemove(entry.key, team),
                deleteButtonTooltipMessage: 'Remove ${entry.key.toUpperCase()} $team',
                deleteIconColor: AppColors.inkSub,
                backgroundColor: AppColors.surface,
                side: const BorderSide(color: AppColors.border),
                shape: const StadiumBorder(),
              ),
        ],
      ),
    );
  }
}
