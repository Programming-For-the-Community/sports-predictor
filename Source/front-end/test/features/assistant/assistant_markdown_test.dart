import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:front_end/features/assistant/assistant_markdown.dart';

Widget _wrap(String text, {double width = 360}) => MaterialApp(
      home: Scaffold(body: Center(child: SizedBox(width: width, child: AssistantMarkdown(text)))),
    );

String _plain(WidgetTester tester) => tester
    .widgetList<RichText>(find.byType(RichText))
    .map((r) => r.text.toPlainText())
    .join('\n');

void main() {
  testWidgets('bold, lists and headings render without their Markdown symbols', (tester) async {
    await tester.pumpWidget(_wrap('**2,566 blocked requests** in the last 30 days.\n\n### Top locations\n\n- United States\n- Hong Kong'));

    final text = _plain(tester);
    expect(text, contains('2,566 blocked requests in the last 30 days.'));
    expect(text, contains('TOP LOCATIONS'));
    expect(text, contains('United States'));
    expect(text, isNot(contains('**')));
    expect(text, isNot(contains('###')));
  });

  testWidgets('a table renders as a grid, numbers right-aligned', (tester) async {
    await tester.pumpWidget(_wrap('| Country | Blocks | Share |\n|---|---|---|\n| United States | 2,054 | 80.0% |\n| Hong Kong | 277 | 10.8% |'));

    expect(find.byType(Table), findsOneWidget);
    expect(_plain(tester), isNot(contains('|')));
    expect(find.text('COUNTRY'), findsOneWidget);
    final blocks = tester.widget<RichText>(find.byWidgetPredicate((w) => w is RichText && w.text.toPlainText() == '2,054'));
    expect(blocks.textAlign, TextAlign.right);
    final country = tester.widget<RichText>(find.byWidgetPredicate((w) => w is RichText && w.text.toPlainText() == 'United States'));
    expect(country.textAlign, TextAlign.left);
  });

  testWidgets('a wide table scrolls sideways inside a phone-width card instead of overflowing', (tester) async {
    await tester.pumpWidget(_wrap(
      '| Game | Pick | Win % | Margin | Total |\n|---|---|---|---|---|\n| Kansas City Chiefs @ New York Jets | KC -9.1 | 77% | 9.1 | 45.2 |',
      width: 320,
    ));

    expect(find.byType(SingleChildScrollView), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('a plain sentence stays a sentence', (tester) async {
    await tester.pumpWidget(_wrap('The model expects Kansas City to win about 77 times in 100.'));

    expect(find.byType(Table), findsNothing);
    expect(_plain(tester), 'The model expects Kansas City to win about 77 times in 100.');
  });
}
