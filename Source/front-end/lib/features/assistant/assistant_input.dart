import 'package:flutter/material.dart';

import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';

/// The question box, its send button, and the questions-left line above it.
/// At the daily limit the box is disabled and the line turns amber.
class AssistantInput extends StatefulWidget {
  const AssistantInput({
    super.key,
    required this.hint,
    required this.enabled,
    required this.quotaReached,
    required this.questionsLeft,
    required this.dailyLimit,
    required this.isAdmin,
    required this.onSend,
  });

  final String hint;
  final bool enabled;
  final bool quotaReached;
  final int? questionsLeft;
  final int? dailyLimit;
  final bool isAdmin;
  final ValueChanged<String> onSend;

  @override
  State<AssistantInput> createState() => _AssistantInputState();
}

class _AssistantInputState extends State<AssistantInput> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _send() {
    final text = _controller.text.trim();
    if (text.isEmpty || !widget.enabled || widget.quotaReached) return;
    _controller.clear();
    widget.onSend(text);
  }

  String? get _quotaLine {
    if (widget.quotaReached) return 'DAILY LIMIT REACHED · RESETS AT MIDNIGHT ET';
    if (widget.questionsLeft == null || widget.dailyLimit == null) return null;
    final prefix = widget.isAdmin ? 'ADMIN · ' : '';
    return '$prefix${widget.questionsLeft} OF ${widget.dailyLimit} QUESTIONS LEFT TODAY';
  }

  @override
  Widget build(BuildContext context) {
    final canType = widget.enabled && !widget.quotaReached;
    final quotaLine = _quotaLine;
    return Container(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 20),
      decoration: const BoxDecoration(border: Border(top: BorderSide(color: AppColors.border))),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          if (quotaLine != null) ...[
            Text(quotaLine, style: AppTextStyles.microLabel(color: widget.quotaReached ? AppColors.warn : AppColors.inkSub)),
            const SizedBox(height: 8),
          ],
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _controller,
                  enabled: canType,
                  maxLength: 1000,
                  minLines: 1,
                  maxLines: 4,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _send(),
                  style: AppTextStyles.body(color: AppColors.ink),
                  decoration: InputDecoration(
                    hintText: widget.quotaReached ? 'You\'ve used today\'s questions' : widget.hint,
                    hintStyle: AppTextStyles.body(color: AppColors.inkSub),
                    counterText: '',
                    filled: true,
                    fillColor: AppColors.surface,
                    contentPadding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
                    border: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: const BorderSide(color: AppColors.borderRaised)),
                    enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: const BorderSide(color: AppColors.borderRaised)),
                    focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(24), borderSide: const BorderSide(color: AppColors.cyan)),
                  ),
                ),
              ),
              const SizedBox(width: 10),
              IconButton.filled(
                tooltip: 'Send',
                onPressed: canType ? _send : null,
                style: IconButton.styleFrom(
                  backgroundColor: AppColors.cyan,
                  disabledBackgroundColor: AppColors.surface,
                  minimumSize: const Size(48, 48),
                ),
                icon: Icon(Icons.arrow_upward, color: canType ? AppColors.bg : AppColors.inkSub),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
