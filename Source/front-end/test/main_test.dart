import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:front_end/app.dart';
import 'package:front_end/main.dart' as entrypoint;

void main() {
  testWidgets('boots the app via runApp without crashing', (tester) async {
    SharedPreferences.setMockInitialValues({});

    entrypoint.main();
    await tester.pump();

    expect(find.byType(App), findsOneWidget);
  });
}
