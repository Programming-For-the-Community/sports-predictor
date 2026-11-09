import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:markdown/markdown.dart' as md;

import '../../core/theme/app_colors.dart';
import '../../core/theme/app_text_styles.dart';

/// An assistant answer's Markdown: paragraphs, bold and italics, lists,
/// headings (as small section labels) and tables. Tables of up to
/// [fittedTableColumns] columns fill the card; wider ones scroll sideways
/// inside it. Anything else renders as its plain text.
class AssistantMarkdown extends StatelessWidget {
  const AssistantMarkdown(this.text, {super.key});

  final String text;

  static const fittedTableColumns = 3;

  @override
  Widget build(BuildContext context) {
    final nodes = md.Document(extensionSet: md.ExtensionSet.gitHubFlavored, encodeHtml: false).parseLines(text.split('\n'));
    final blocks = [for (final node in nodes) _block(node)];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (var i = 0; i < blocks.length; i++) ...[if (i > 0) const SizedBox(height: 12), blocks[i]],
      ],
    );
  }
}

TextStyle get _body => AppTextStyles.body().copyWith(height: 1.45);
TextStyle get _label => AppTextStyles.microLabel(color: AppColors.inkSub);
TextStyle get _mono => GoogleFonts.ibmPlexMono(fontSize: 13.5, fontWeight: FontWeight.w500, color: AppColors.ink);

Widget _block(md.Node node) {
  if (node is! md.Element) return Text(node.textContent, style: _body);
  return switch (node.tag) {
    'h1' || 'h2' || 'h3' || 'h4' || 'h5' || 'h6' => Text(node.textContent.toUpperCase(), style: _label),
    'ul' || 'ol' => _List(node),
    'table' => _MarkdownTable(node),
    'hr' => const Divider(color: AppColors.border, height: 1),
    _ => Text.rich(TextSpan(children: _inline(node.children ?? const [])), style: _body),
  };
}

List<InlineSpan> _inline(List<md.Node> nodes, {TextStyle? style}) => [
      for (final node in nodes)
        if (node is md.Element)
          switch (node.tag) {
            'strong' => TextSpan(
                children: _inline(node.children ?? const [], style: (style ?? const TextStyle()).copyWith(fontWeight: FontWeight.w700, color: AppColors.ink))),
            'em' => TextSpan(children: _inline(node.children ?? const [], style: (style ?? const TextStyle()).copyWith(fontStyle: FontStyle.italic))),
            'code' => TextSpan(text: node.textContent, style: _mono),
            'br' => const TextSpan(text: '\n'),
            _ => TextSpan(children: _inline(node.children ?? const [], style: style)),
          }
        else
          TextSpan(text: node.textContent, style: style),
    ];

class _List extends StatelessWidget {
  const _List(this.list);

  final md.Element list;

  @override
  Widget build(BuildContext context) {
    final items = (list.children ?? const []).whereType<md.Element>().toList();
    final ordered = list.tag == 'ol';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (var i = 0; i < items.length; i++)
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(width: 20, child: Text(ordered ? '${i + 1}.' : '•', style: _body.copyWith(color: AppColors.inkSub))),
                Expanded(child: Text.rich(TextSpan(children: _inline(_itemContent(items[i]))), style: _body)),
              ],
            ),
          ),
      ],
    );
  }

  /// A loose list wraps each item in a paragraph; use what's inside it.
  static List<md.Node> _itemContent(md.Element item) {
    final children = item.children ?? const [];
    if (children.length == 1 && children.first is md.Element && (children.first as md.Element).tag == 'p') {
      return (children.first as md.Element).children ?? const [];
    }
    return children;
  }
}

final _numeric = RegExp(r'^[-+]?\$?[\d,]+(\.\d+)?\s?%?$');

class _MarkdownTable extends StatelessWidget {
  const _MarkdownTable(this.table);

  final md.Element table;

  static List<List<md.Element>> _rows(md.Element table, String section) => [
        for (final part in (table.children ?? const []).whereType<md.Element>().where((e) => e.tag == section))
          for (final row in (part.children ?? const []).whereType<md.Element>())
            (row.children ?? const []).whereType<md.Element>().toList(),
      ];

  @override
  Widget build(BuildContext context) {
    final header = _rows(table, 'thead').firstOrNull ?? const <md.Element>[];
    final body = _rows(table, 'tbody');
    final columns = [header.length, ...body.map((r) => r.length)].fold(0, (a, b) => a > b ? a : b);
    if (columns == 0) return const SizedBox.shrink();
    final numeric = [
      for (var c = 0; c < columns; c++)
        body.isNotEmpty && body.every((r) => c >= r.length || r[c].textContent.trim().isEmpty || _numeric.hasMatch(r[c].textContent.trim())),
    ];

    Widget cell(md.Element? element, int column, {required bool head}) {
      final align = numeric[column] ? TextAlign.right : TextAlign.left;
      final content = element == null
          ? const Text('')
          : head
              ? Text(element.textContent.toUpperCase(), textAlign: align, style: _label)
              : Text.rich(TextSpan(children: _inline(element.children ?? const [])), textAlign: align, style: numeric[column] ? _mono : _body.copyWith(fontSize: 14));
      return Padding(padding: EdgeInsets.fromLTRB(column == 0 ? 0 : 12, 7, 0, 7), child: content);
    }

    TableRow row(List<md.Element> cells, {required bool head}) => TableRow(
          decoration: const BoxDecoration(border: Border(bottom: BorderSide(color: AppColors.border))),
          children: [for (var c = 0; c < columns; c++) cell(c < cells.length ? cells[c] : null, c, head: head)],
        );

    final fitted = columns <= AssistantMarkdown.fittedTableColumns;
    final grid = Table(
      defaultVerticalAlignment: TableCellVerticalAlignment.middle,
      columnWidths: fitted ? {0: const FlexColumnWidth()} : null,
      defaultColumnWidth: const IntrinsicColumnWidth(),
      children: [if (header.isNotEmpty) row(header, head: true), for (final r in body) row(r, head: false)],
    );
    return fitted ? grid : SingleChildScrollView(scrollDirection: Axis.horizontal, child: grid);
  }
}
