import 'package:flutter/material.dart';

import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';
import 'assistant_markdown.dart';
import 'assistant_models.dart';

/// The conversation: the user's questions right-aligned, answers in cards
/// with a quiet "FROM" line naming what they were read from, a progress line
/// while tools run, and starter questions before the first message.
class AssistantMessages extends StatelessWidget {
  const AssistantMessages({
    super.key,
    required this.messages,
    required this.busy,
    required this.progressTool,
    required this.suggestions,
    required this.onSuggestion,
    this.contextLabel,
  });

  final List<ChatMessage> messages;
  final bool busy;
  final String? progressTool;
  final List<String> suggestions;
  final ValueChanged<String> onSuggestion;
  final String? contextLabel;

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 16),
      children: [
        if (contextLabel != null) _ContextChip(label: contextLabel!),
        for (final message in messages) message.fromUser ? _Question(message.text) : _Answer(message),
        if (busy) _Working(progressTool),
        if (messages.isEmpty && !busy)
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final suggestion in suggestions)
                ActionChip(
                  label: Text(suggestion, style: AppTextStyles.body(color: AppColors.inkMid).copyWith(fontSize: 13)),
                  onPressed: () => onSuggestion(suggestion),
                  backgroundColor: Colors.transparent,
                  side: const BorderSide(color: AppColors.borderRaised),
                  shape: const StadiumBorder(),
                ),
            ],
          ),
      ],
    );
  }
}

class _ContextChip extends StatelessWidget {
  const _ContextChip({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: 16),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: AppColors.cyan.withValues(alpha: 0.08),
          border: Border.all(color: AppColors.cyan.withValues(alpha: 0.6)),
          borderRadius: BorderRadius.circular(999),
        ),
        child: Text('ABOUT  $label', style: AppTextStyles.microLabel(color: AppColors.ink)),
      ),
    );
  }
}

class _Question extends StatelessWidget {
  const _Question(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerRight,
      child: Container(
        margin: const EdgeInsets.only(bottom: 14, left: 48),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        decoration: const BoxDecoration(
          color: Color(0x14FFFFFF),
          borderRadius: BorderRadius.only(
            topLeft: Radius.circular(16), topRight: Radius.circular(16), bottomLeft: Radius.circular(16), bottomRight: Radius.circular(4),
          ),
        ),
        child: Text(text, style: AppTextStyles.body(color: AppColors.ink)),
      ),
    );
  }
}

class _Answer extends StatelessWidget {
  const _Answer(this.message);
  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 14),
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: AppColors.surface,
        border: Border.all(color: AppColors.border),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SelectionArea(child: AssistantMarkdown(message.text)),
          if (message.sources.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text('FROM · ${message.sources.map(_sourceLabel).join(' · ')}', style: AppTextStyles.microLabel(color: AppColors.inkSub)),
          ],
        ],
      ),
    );
  }
}

class _Working extends StatelessWidget {
  const _Working(this.tool);
  final String? tool;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Row(
        children: [
          const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.cyan)),
          const SizedBox(width: 10),
          Flexible(
            child: Text(
              tool == null ? 'THINKING' : 'CHECKING ${_sourceLabel(tool!)}',
              style: AppTextStyles.microLabel(color: AppColors.inkSub),
            ),
          ),
        ],
      ),
    );
  }
}

/// "get_model_performance" -> "MODEL PERFORMANCE".
String _sourceLabel(String tool) => tool.replaceFirst(RegExp(r'^(get|list)_'), '').replaceAll('_', ' ').toUpperCase();
