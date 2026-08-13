import 'package:flutter/material.dart';

import '../theme/app_colors.dart';
import '../theme/app_text_styles.dart';

/// Shared by season_page.dart's standings and event_list_page.dart's
/// event list -- both group by the same backend-provided conference
/// field (see static/conference_order.dart's own doc comment) and want
/// the identical filter control.
///
/// Owns a TextEditingController, but only ever sets its text
/// programmatically once (the clear button) -- built once (`late final`,
/// not re-created from `widget.value` on every rebuild), so ordinary
/// typing is otherwise exactly as uncontrolled as a bare TextField and
/// never fights the user's own cursor position. Without a controller at
/// all, the clear button's `onChanged('')` call only reset the caller's
/// own filter state, not the field's own still-visible text -- this is
/// what actually clears the displayed text too.
class ConferenceFilterField extends StatefulWidget {
  const ConferenceFilterField({super.key, required this.value, required this.onChanged});

  final String value;
  final ValueChanged<String> onChanged;

  @override
  State<ConferenceFilterField> createState() => _ConferenceFilterFieldState();
}

class _ConferenceFilterFieldState extends State<ConferenceFilterField> {
  late final TextEditingController _controller = TextEditingController(text: widget.value);

  void _clear() {
    _controller.clear();
    widget.onChanged('');
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: _controller,
      onChanged: widget.onChanged,
      style: AppTextStyles.body(color: AppColors.ink),
      decoration: InputDecoration(
        hintText: 'Filter by conference...',
        hintStyle: AppTextStyles.body(color: AppColors.inkMute),
        prefixIcon: Icon(Icons.search, size: 18, color: AppColors.inkMute),
        suffixIcon: widget.value.isEmpty
            ? null
            : IconButton(
                icon: Icon(Icons.close, size: 16, color: AppColors.inkMute),
                onPressed: _clear,
              ),
        isDense: true,
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        filled: true,
        fillColor: AppColors.surface,
        border: OutlineInputBorder(borderRadius: BorderRadius.circular(999), borderSide: BorderSide(color: AppColors.border)),
        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(999), borderSide: BorderSide(color: AppColors.border)),
        focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(999), borderSide: BorderSide(color: AppColors.cyan)),
      ),
    );
  }
}
