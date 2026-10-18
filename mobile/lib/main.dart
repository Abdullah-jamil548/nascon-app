import 'dart:async';
import 'dart:convert';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

import 'local_store.dart';

void main() {
  runApp(const LeagueApp());
}

class LeagueApp extends StatefulWidget {
  const LeagueApp({super.key});

  @override
  State<LeagueApp> createState() => _LeagueAppState();
}

class _LeagueAppState extends State<LeagueApp> {
  final api = Api();
  Color primary = const Color(0xFF0B3D2E);
  Color secondary = const Color(0xFFF5C518);

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'NaSCon League',
      debugShowCheckedModeBanner: false,
      theme: _theme(primary, secondary, Brightness.light),
      darkTheme: _theme(primary, secondary, Brightness.dark),
      home: RootPage(
        api: api,
        onTenant: (nextPrimary, nextSecondary) {
          setState(() {
            primary = nextPrimary;
            secondary = nextSecondary;
          });
        },
      ),
    );
  }
}

ThemeData _theme(Color primary, Color secondary, Brightness brightness) {
  final scheme = ColorScheme.fromSeed(seedColor: primary, primary: primary, secondary: secondary, brightness: brightness);
  final outline = RoundedRectangleBorder(borderRadius: BorderRadius.circular(14));
  return ThemeData(
    colorScheme: scheme,
    useMaterial3: true,
    scaffoldBackgroundColor: brightness == Brightness.dark ? const Color(0xFF101412) : const Color(0xFFF2F4F3),
    appBarTheme: AppBarTheme(
      backgroundColor: scheme.primary,
      foregroundColor: Colors.white,
      elevation: 0,
      scrolledUnderElevation: 0,
      titleTextStyle: const TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w700, letterSpacing: 0.2),
    ),
    cardTheme: CardThemeData(
      elevation: 0,
      color: scheme.surface,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: scheme.outlineVariant.withValues(alpha: 0.7)),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: scheme.surface,
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 14),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: scheme.outlineVariant)),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: scheme.outlineVariant)),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(minimumSize: const Size(48, 48), textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(48), shape: outline),
    ),
    navigationBarTheme: NavigationBarThemeData(
      height: 68,
      backgroundColor: scheme.surface,
      indicatorColor: scheme.primary.withValues(alpha: 0.12),
      labelTextStyle: const WidgetStatePropertyAll(TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
    ),
    snackBarTheme: SnackBarThemeData(behavior: SnackBarBehavior.floating, shape: outline),
    dividerTheme: DividerThemeData(color: scheme.outlineVariant.withValues(alpha: 0.6)),
  );
}

class Api {
  final storage = const FlutterSecureStorage();
  final local = LocalStore();
  static const _definedBase = String.fromEnvironment('API_BASE');
  String base = _definedBase.isNotEmpty
      ? _definedBase
      : (!kIsWeb && defaultTargetPlatform == TargetPlatform.android ? 'http://10.0.2.2:8080' : 'http://127.0.0.1:8080');
  String? token;
  String role = '';
  String name = '';
  String tenantName = '';
  String link = 'Syncing';
  String? highlight;
  void Function()? onExpired;

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    base = _definedBase.isNotEmpty ? _definedBase : (prefs.getString('base') ?? base);
    token = await storage.read(key: 'token');
    role = await storage.read(key: 'role') ?? '';
    name = await storage.read(key: 'name') ?? '';
    tenantName = await storage.read(key: 'tenant_name') ?? '';
  }

  Future<bool> phoneHasNetwork() async {
    try {
      final results = await Connectivity().checkConnectivity();
      if (results.isEmpty) return true;
      return results.any((item) => item != ConnectivityResult.none);
    } catch (_) {
      return true;
    }
  }

  Future<bool> probe() async {
    final online = await phoneHasNetwork();
    link = online ? 'Online' : 'Offline';
    return online;
  }

  Future<void> saveBase(String value) async {
    base = value.trim().replaceAll(RegExp(r'/$'), '');
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('base', base);
  }

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (token != null) 'Authorization': 'Bearer $token',
      };

  Future<Map<String, dynamic>> send(String method, String path, [Map<String, dynamic>? body]) async {
    try {
      final uri = Uri.parse('$base$path');
      late http.Response response;
      if (method == 'GET') {
        response = await http.get(uri, headers: _headers).timeout(const Duration(seconds: 8));
      } else {
        response = await http.post(uri, headers: _headers, body: jsonEncode(body ?? {})).timeout(const Duration(seconds: 8));
      }
      final decoded = response.body.isEmpty ? <String, dynamic>{} : jsonDecode(response.body) as Map<String, dynamic>;
      if (response.statusCode >= 400) {
        final error = decoded['error'];
        final message = error is Map ? (error['message'] ?? 'Request failed') : 'Request failed';
        if (response.statusCode == 401 && token != null && !path.contains('/auth/login')) {
          await logout();
          onExpired?.call();
          throw ApiException('Your session ended. Sign in again.');
        }
        throw ApiException(message.toString());
      }
      link = await phoneHasNetwork() ? 'Online' : 'Offline';
      return decoded;
    } catch (error) {
      if (error is ApiException) rethrow;
      if (!await phoneHasNetwork()) {
        link = 'Offline';
        throw ApiException('You are offline. Saved screens are still available.');
      }
      throw ApiException('The server did not answer. Check the server address.');
    }
  }

  Future<void> login(String email, String password) async {
    final data = await send('POST', '/api/v1/auth/login', {'email': email, 'password': password});
    token = data['token'] as String;
    role = data['role'] as String;
    name = data['name']?.toString() ?? '';
    await storage.write(key: 'token', value: token);
    await storage.write(key: 'role', value: role);
    await storage.write(key: 'name', value: name);
    final tenant = data['tenant'];
    if (tenant is Map) {
      tenantName = tenant['name']?.toString() ?? '';
      await storage.write(key: 'tenant_name', value: tenantName);
      await local.save('tenant', Map<String, dynamic>.from(tenant));
    }
  }

  Future<void> logout() async {
    token = null;
    role = '';
    name = '';
    tenantName = '';
    await storage.deleteAll();
  }
}

class ApiException implements Exception {
  ApiException(this.message);
  final String message;
}

class RootPage extends StatefulWidget {
  const RootPage({super.key, required this.api, required this.onTenant});
  final Api api;
  final void Function(Color primary, Color secondary) onTenant;

  @override
  State<RootPage> createState() => _RootPageState();
}

class _RootPageState extends State<RootPage> {
  bool ready = false;
  bool signedIn = false;
  String? guestLink;

  @override
  void initState() {
    super.initState();
    _boot();
  }

  Future<void> _boot() async {
    widget.api.onExpired = () {
      if (mounted) setState(() => signedIn = false);
    };
    await widget.api.load();
    String? link;
    try {
      link = await const MethodChannel('nascon/link').invokeMethod<String>('initial');
    } catch (_) {}
    if (link != null && link.startsWith('nascon://')) {
      setState(() {
        guestLink = link;
        ready = true;
      });
      return;
    }
    final tenant = await widget.api.local.readMap('tenant');
    if (tenant != null) {
      widget.onTenant(_color(tenant['primary_color']), _color(tenant['secondary_color']));
    }
    setState(() {
      signedIn = widget.api.token != null;
      ready = true;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (!ready) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    if (guestLink != null) {
      final costream = guestLink!.contains('cs-session');
      return Scaffold(
        backgroundColor: const Color(0xFF101412),
        body: SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(costream ? 'Co-Stream' : 'Live match', style: const TextStyle(color: Colors.white, fontSize: 28, fontWeight: FontWeight.w800)),
                const SizedBox(height: 16),
                Container(
                  height: 180,
                  width: double.infinity,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(color: const Color(0xFF17211C), borderRadius: BorderRadius.circular(16)),
                  child: const Text('LIVE', style: TextStyle(color: Color(0xFFF5C518), fontWeight: FontWeight.w800, letterSpacing: 1.4)),
                ),
                const SizedBox(height: 16),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(vertical: 18),
                  alignment: Alignment.center,
                  decoration: BoxDecoration(color: const Color(0xFF0B3D2E), borderRadius: BorderRadius.circular(16)),
                  child: Text(costream ? 'Grid' : '1 - 0', style: const TextStyle(color: Colors.white, fontSize: 32, fontWeight: FontWeight.w800)),
                ),
                const SizedBox(height: 12),
                Text(guestLink!, key: const Key('deep-link-target'), style: const TextStyle(color: Color(0xFFB7C4BC))),
              ],
            ),
          ),
        ),
      );
    }
    if (!signedIn) {
      return LoginPage(
        api: widget.api,
        onDone: (primary, secondary) {
          widget.onTenant(primary, secondary);
          setState(() => signedIn = true);
        },
      );
    }
    return HomePage(
      api: widget.api,
      onLogout: () => setState(() => signedIn = false),
    );
  }
}

class LoginPage extends StatefulWidget {
  const LoginPage({super.key, required this.api, required this.onDone});
  final Api api;
  final void Function(Color primary, Color secondary) onDone;

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final email = TextEditingController(text: 'viewer@nascon.test');
  final password = TextEditingController(text: 'Nascon@26');
  final server = TextEditingController();
  String? error;
  String? connection;
  bool busy = false;
  bool hidePassword = true;

  @override
  void initState() {
    super.initState();
    server.text = widget.api.base;
  }

  Future<void> submit() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.api.saveBase(server.text);
      await widget.api.login(email.text.trim(), password.text);
      final tenant = await widget.api.local.readMap('tenant');
      final primary = _color(tenant?['primary_color']);
      final secondary = _color(tenant?['secondary_color'], const Color(0xFFF5C518));
      widget.onDone(primary, secondary);
    } on ApiException catch (caught) {
      setState(() => error = caught.message);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> testConnection() async {
    setState(() => connection = 'Checking…');
    await widget.api.saveBase(server.text);
    final ok = await widget.api.probe();
    if (mounted) setState(() => connection = ok ? 'Connected' : 'Cannot reach server');
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    const demos = [
      ('Viewer', 'viewer@nascon.test'),
      ('Player', 'player@nascon.test'),
      ('Manager', 'manager@nascon.test'),
      ('Organizer', 'organizer@nascon.test'),
      ('Admin', 'admin@nascon.test'),
    ];
    return Scaffold(
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 20, 20, 28),
          children: [
            const SizedBox(height: 8),
            const Center(child: _BrandMark()),
            const SizedBox(height: 16),
            const Text('NaSCon League', textAlign: TextAlign.center, style: TextStyle(fontSize: 28, fontWeight: FontWeight.w800, letterSpacing: -0.4)),
            const SizedBox(height: 4),
            Text('Matchday, table and the club store', textAlign: TextAlign.center, style: TextStyle(color: scheme.onSurfaceVariant)),
            const SizedBox(height: 22),
            _Panel(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 16, 16, 16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Demo account', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w800, color: scheme.onSurfaceVariant)),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (final demo in demos)
                          FilterChip(
                            label: Text(demo.$1),
                            selected: email.text == demo.$2,
                            onSelected: busy ? null : (_) => setState(() => email.text = demo.$2),
                            showCheckmark: false,
                          ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    TextField(
                      controller: email,
                      decoration: const InputDecoration(labelText: 'Email', prefixIcon: Icon(Icons.mail_outline)),
                      keyboardType: TextInputType.emailAddress,
                      textInputAction: TextInputAction.next,
                      autofillHints: const [AutofillHints.email],
                      onChanged: (_) => setState(() {}),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: password,
                      decoration: InputDecoration(
                        labelText: 'Password',
                        prefixIcon: const Icon(Icons.lock_outline),
                        suffixIcon: IconButton(
                          tooltip: hidePassword ? 'Show password' : 'Hide password',
                          onPressed: () => setState(() => hidePassword = !hidePassword),
                          icon: Icon(hidePassword ? Icons.visibility_outlined : Icons.visibility_off_outlined),
                        ),
                      ),
                      obscureText: hidePassword,
                      textInputAction: TextInputAction.done,
                      onSubmitted: (_) => busy ? null : submit(),
                      autofillHints: const [AutofillHints.password],
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: server,
                      decoration: const InputDecoration(labelText: 'Server', prefixIcon: Icon(Icons.dns_outlined)),
                    ),
                    const SizedBox(height: 6),
                    Text('USB phone: 127.0.0.1:8080', style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 12)),
                    const SizedBox(height: 4),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: TextButton.icon(
                        onPressed: busy ? null : testConnection,
                        icon: Icon(connection == 'Connected' ? Icons.check_circle : Icons.wifi_tethering, size: 18),
                        label: Text(connection ?? 'Test connection'),
                      ),
                    ),
                    if (error != null) ...[
                      const SizedBox(height: 4),
                      _Note(text: error!, error: true),
                    ],
                    const SizedBox(height: 8),
                    SizedBox(
                      width: double.infinity,
                      height: 50,
                      child: FilledButton(onPressed: busy ? null : submit, child: Text(busy ? 'Signing in…' : 'Sign in')),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _BrandMark extends StatelessWidget {
  const _BrandMark();

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return SizedBox(
      width: 96,
      height: 96,
      child: CustomPaint(painter: _LogoPainter(green: scheme.primary, gold: scheme.secondary)),
    );
  }
}

class _LogoPainter extends CustomPainter {
  _LogoPainter({required this.green, required this.gold});
  final Color green;
  final Color gold;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = size.shortestSide / 2;
    canvas.drawCircle(center, radius, Paint()..color = gold);
    canvas.drawCircle(center, radius - 5, Paint()..color = green);
    final shield = Path()
      ..moveTo(center.dx, center.dy - radius * 0.46)
      ..lineTo(center.dx + radius * 0.38, center.dy - radius * 0.22)
      ..lineTo(center.dx + radius * 0.32, center.dy + radius * 0.16)
      ..quadraticBezierTo(center.dx, center.dy + radius * 0.52, center.dx - radius * 0.32, center.dy + radius * 0.16)
      ..lineTo(center.dx - radius * 0.38, center.dy - radius * 0.22)
      ..close();
    canvas.drawPath(shield, Paint()..color = Colors.white.withValues(alpha: 0.14));
    canvas.drawPath(shield, Paint()..color = gold..style = PaintingStyle.stroke..strokeWidth = 1.4);
    final letter = TextPainter(
      text: const TextSpan(text: 'N', style: TextStyle(color: Colors.white, fontSize: 28, fontWeight: FontWeight.w800, height: 1)),
      textDirection: TextDirection.ltr,
    )..layout();
    letter.paint(canvas, center - Offset(letter.width / 2, letter.height / 2 + 1));
  }

  @override
  bool shouldRepaint(covariant _LogoPainter oldDelegate) => oldDelegate.green != green || oldDelegate.gold != gold;
}

class HomePage extends StatefulWidget {
  const HomePage({super.key, required this.api, required this.onLogout});
  final Api api;
  final VoidCallback onLogout;

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> with WidgetsBindingObserver {
  int tab = 0;
  List<Map<String, dynamic>> queue = [];
  List<String> reports = [];
  StreamSubscription<List<ConnectivityResult>>? _net;
  Timer? _ticker;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _loadQueue();
    _net = Connectivity().onConnectivityChanged.listen((results) async {
      final connected = results.any((item) => item != ConnectivityResult.none);
      if (!connected) {
        await Future<void>.delayed(const Duration(milliseconds: 700));
        if (!mounted || await widget.api.phoneHasNetwork()) return;
        setState(() => widget.api.link = 'Offline');
        return;
      }
      if (!mounted) return;
      setState(() => widget.api.link = 'Online');
      _sync(automatic: true);
    });
    _ticker = Timer.periodic(const Duration(seconds: 3), (_) => _watch());
    _sync(automatic: true);
  }

  @override
  void dispose() {
    _ticker?.cancel();
    _net?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  Future<void> _watch() async {
    if (!mounted || widget.api.link == 'Syncing') return;
    final before = widget.api.link;
    final online = await widget.api.probe();
    if (!mounted) return;
    if (online && before == 'Offline') {
      await _sync(automatic: true);
      return;
    }
    if (widget.api.link != before) setState(() {});
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _sync(automatic: true);
  }

  Future<void> _loadQueue() async {
    final rows = await widget.api.local.pending();
    if (mounted) setState(() => queue = rows);
  }

  Future<void> _sync({bool automatic = false}) async {
    final rows = await widget.api.local.pending();
    if (mounted) setState(() => widget.api.link = 'Syncing');
    final online = await widget.api.probe();
    if (!online) {
      await _loadQueue();
      if (!automatic && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('No connection. Changes stay on this phone.')));
      }
      return;
    }
    if (rows.isEmpty) {
      if (mounted) setState(() => widget.api.link = 'Online');
      if (!automatic && mounted) {
        ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('You are online. Nothing is waiting to sync.')));
      }
      return;
    }
    var synced = false;
    try {
      final actions = rows.map((row) => row['action'] as Map<String, dynamic>).toList();
      final result = await widget.api.send('POST', '/api/v1/offline-sync', {'device_id': 'phone-1', 'actions': actions});
      final results = (result['results'] as List).cast<Map<String, dynamic>>();
      for (var i = 0; i < rows.length; i++) {
        final item = results[i];
        final status = item['status'];
        final reason = item['error_code'];
        final text = status == 'applied'
            ? 'Applied'
            : status == 'duplicate'
                ? 'Already synced'
                : 'Not applied: ${_plain(reason?.toString())}';
        await widget.api.local.mark(rows[i]['id'] as int, text);
        reports.insert(0, '${rows[i]['label']}: $text');
      }
      await widget.api.local.clearDone();
      widget.api.link = 'Online';
      synced = true;
    } on ApiException {
      final stillOnline = await widget.api.probe();
      widget.api.link = stillOnline ? 'Online' : 'Offline';
    }
    await _loadQueue();
    if (!automatic && mounted) {
      final text = synced
          ? 'Synced'
          : widget.api.link == 'Offline'
              ? 'No connection. Changes stay on this phone.'
              : 'The server did not answer. Your phone is still online.';
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
    }
  }

  @override
  Widget build(BuildContext context) {
    final pages = [
      FixturesPage(api: widget.api, onQueued: _loadQueue, onOpenTab: (index) => setState(() => tab = index)),
      StandingsPage(api: widget.api, onQueued: _loadQueue, onOpenTab: (index) => setState(() => tab = index)),
      StorePage(api: widget.api),
      ClubPage(api: widget.api, onQueued: _loadQueue),
      MorePage(api: widget.api, queue: queue, reports: reports, onSync: () => _sync(), onLogout: widget.onLogout, onChanged: () => setState(() {})),
    ];
    final role = widget.api.role.replaceAll('_', ' ');
    final title = widget.api.tenantName.isEmpty ? 'NaSCon League' : widget.api.tenantName;
    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title),
            Text(role.isEmpty ? 'League' : role, style: const TextStyle(color: Colors.white70, fontSize: 12, fontWeight: FontWeight.w500)),
          ],
        ),
        toolbarHeight: 64,
        actions: [
          _SyncChip(link: widget.api.link, onTap: () => _sync()),
        ],
      ),
      body: Column(
        children: [
          if (widget.api.link == 'Offline')
            const Material(
              color: Color(0xFFFFF4E5),
              child: ListTile(
                dense: true,
                leading: Icon(Icons.cloud_off),
                title: Text('You are offline. Changes are saved on this phone.'),
              ),
            ),
          if (queue.isNotEmpty)
            Material(
              color: const Color(0xFFFFF6D8),
              child: ListTile(
                dense: true,
                leading: const Icon(Icons.schedule),
                title: Text('${queue.length} waiting to sync'),
                subtitle: Text(queue.map((row) => row['label']).join(', '), maxLines: 1, overflow: TextOverflow.ellipsis),
                onTap: () => setState(() => tab = 4),
              ),
            ),
          Expanded(
            child: IndexedStack(
              index: tab,
              children: pages,
            ),
          ),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: tab,
        onDestinationSelected: (value) => setState(() => tab = value),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.home), label: 'Home'),
          NavigationDestination(icon: Icon(Icons.leaderboard), label: 'Standings'),
          NavigationDestination(icon: Icon(Icons.storefront), label: 'Store'),
          NavigationDestination(icon: Icon(Icons.shield), label: 'Club'),
          NavigationDestination(icon: Icon(Icons.person), label: 'More'),
        ],
      ),
    );
  }
}

class FixturesPage extends StatefulWidget {
  const FixturesPage({super.key, required this.api, required this.onQueued, required this.onOpenTab});
  final Api api;
  final Future<void> Function() onQueued;
  final void Function(int index) onOpenTab;

  @override
  State<FixturesPage> createState() => _FixturesPageState();
}

class _FixturesPageState extends State<FixturesPage> {
  List<dynamic> rows = [];
  String? error;
  bool loading = true;
  String filter = 'All';
  String sport = 'All';
  String league = 'All';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await widget.api.send('GET', '/api/v1/fixtures');
      rows = data['fixtures'] as List? ?? [];
      await widget.api.local.save('fixtures', data);
      error = null;
    } on ApiException catch (caught) {
      final cached = await widget.api.local.readMap('fixtures');
      rows = cached?['fixtures'] as List? ?? [];
      error = rows.isEmpty ? caught.message : null;
    }
    if (mounted) setState(() => loading = false);
  }

  @override
  Widget build(BuildContext context) {
    if (loading) return const _LoadingList();
    if (rows.isEmpty) return const Center(child: Text('No matches yet. Pull to refresh when you are online.'));
    final sports = <String>{'All'};
    final leagues = <String>{'All'};
    for (final raw in rows) {
      final row = raw as Map;
      sports.add('${row['sport_id']}');
      leagues.add('${row['league_id']}');
    }
    final shown = rows.where((raw) {
      final row = raw as Map;
      final status = row['status'];
      if (sport != 'All' && row['sport_id'] != sport) return false;
      if (league != 'All' && row['league_id'] != league) return false;
      if (filter == 'Live') return status == 'in_progress';
      if (filter == 'Upcoming') return status == 'upcoming';
      if (filter == 'Finished') return status == 'completed';
      if (filter == 'Postponed') return status == 'postponed';
      return true;
    }).toList();
    final live = rows.where((raw) => (raw as Map)['status'] == 'in_progress').length;
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: _Panel(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(live == 0 ? 'No match is live' : '$live live now', style: const TextStyle(fontWeight: FontWeight.w800)),
                  const SizedBox(height: 4),
                  Text('${widget.api.name.isEmpty ? 'Welcome' : widget.api.name} · ${widget.api.role.replaceAll('_', ' ')}'),
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 8,
                    children: [
                      ActionChip(label: const Text('Standings'), onPressed: () => widget.onOpenTab(1)),
                      ActionChip(label: const Text('Store'), onPressed: () => widget.onOpenTab(2)),
                      ActionChip(label: const Text('Club'), onPressed: () => widget.onOpenTab(3)),
                    ],
                  ),
                ],
              ),
            ),
          ),
        ),
        SizedBox(
          height: 52,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            children: [
              for (final name in const ['All', 'Live', 'Upcoming', 'Finished', 'Postponed'])
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: FilterChip(
                    label: Text(name),
                    selected: filter == name,
                    onSelected: (_) => setState(() => filter = name),
                  ),
                ),
              for (final name in sports)
                if (name != 'All')
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: FilterChip(
                      label: Text(_pretty(name)),
                      selected: sport == name,
                      onSelected: (_) => setState(() => sport = sport == name ? 'All' : name),
                    ),
                  ),
              for (final name in leagues)
                if (name != 'All')
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: FilterChip(
                      label: Text(_pretty(name)),
                      selected: league == name,
                      onSelected: (_) => setState(() => league = league == name ? 'All' : name),
                    ),
                  ),
            ],
          ),
        ),
        Expanded(
          child: shown.isEmpty
              ? const Center(child: Text('No matches in this view.'))
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView.separated(
                    physics: const AlwaysScrollableScrollPhysics(),
                    padding: const EdgeInsets.all(16),
                    itemCount: shown.length,
                    separatorBuilder: (_, index) => const SizedBox(height: 10),
                    itemBuilder: (context, index) {
                      final match = Map<String, dynamic>.from(shown[index] as Map);
                      final score = Map<String, dynamic>.from(match['score'] as Map? ?? {});
                      final home = '${match['home']}';
                      final away = '${match['away']}';
                      return _FixtureCard(
                        status: '${match['status']}',
                        when: _when(match['starts_at']?.toString()),
                        home: home,
                        away: away,
                        homeScore: '${score['home'] ?? 0}',
                        awayScore: '${score['away'] ?? 0}',
                        onTap: () => Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => MatchPage(
                              api: widget.api,
                              matchId: match['match_id'] as String,
                              onQueued: widget.onQueued,
                              onOpenStore: () => widget.onOpenTab(2),
                            ),
                          ),
                        ),
                      );
                    },
                  ),
                ),
        ),
      ],
    );
  }
}

class MatchPage extends StatefulWidget {
  const MatchPage({super.key, required this.api, required this.matchId, required this.onQueued, required this.onOpenStore});
  final Api api;
  final String matchId;
  final Future<void> Function() onQueued;
  final VoidCallback onOpenStore;

  @override
  State<MatchPage> createState() => _MatchPageState();
}

class _MatchPageState extends State<MatchPage> {
  Map<String, dynamic>? match;
  String? message;
  String scoringSide = 'home';
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    _load();
    _poll = Timer.periodic(const Duration(seconds: 8), (_) => _load());
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      match = await widget.api.send('GET', '/api/v1/matches/${widget.matchId}');
      await widget.api.local.save('match-${widget.matchId}', match!);
    } on ApiException catch (caught) {
      match = await widget.api.local.readMap('match-${widget.matchId}');
      message = match == null ? caught.message : 'Showing the saved match.';
    }
    if (mounted) setState(() {});
  }

  Future<void> _score(String teamId, String eventType) async {
    final version = match?['version'] ?? 0;
    final eventId = 'evt-${DateTime.now().millisecondsSinceEpoch}';
    final action = {
      'action_id': 'act-$eventId',
      'type': 'score_event',
      'queued_at': '${DateTime.now().toUtc().toIso8601String().split('.').first}Z',
      'base_version': version,
      'payload': {
        'event_id': eventId,
        'match_id': widget.matchId,
        'type': 'score',
        'payload': {'team_id': teamId, 'event_type': eventType},
      },
    };
    if (widget.api.link == 'Offline') {
      await widget.api.local.enqueue(action, 'Score $eventType');
      await widget.onQueued();
      setState(() => message = 'Saved on this phone. It will sync when you are back online.');
      return;
    }
    try {
      if (match?['status'] == 'upcoming') {
        await widget.api.send('POST', '/api/v1/events', {
          'event_id': 'start-$eventId',
          'match_id': widget.matchId,
          'type': 'match_start',
          'payload': {},
        });
      }
      await widget.api.send('POST', '/api/v1/events', action['payload'] as Map<String, dynamic>);
      await _load();
      setState(() => message = 'Score saved.');
    } on ApiException catch (caught) {
      await widget.api.local.enqueue(action, 'Score $eventType');
      await widget.onQueued();
      setState(() => message = caught.message);
    }
  }

  Future<void> _compensate() async {
    final events = (match?['events'] as List? ?? []);
    Map? last;
    for (final event in events) {
      final row = Map<String, dynamic>.from(event as Map);
      if (row['type'] == 'score' && row['compensated'] != true) last = row;
    }
    if (last == null) {
      setState(() => message = 'There is no score to correct.');
      return;
    }
    try {
      await widget.api.send('POST', '/api/v1/events', {
        'event_id': 'comp-${DateTime.now().millisecondsSinceEpoch}',
        'match_id': widget.matchId,
        'type': 'compensation',
        'payload': {'compensates': last['event_id'], 'reason': 'Disallowed'},
      });
      await _load();
      setState(() => message = 'Correction added. The old event stays in the timeline.');
    } on ApiException catch (caught) {
      setState(() => message = caught.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (match == null) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    final home = Map<String, dynamic>.from(match!['home'] as Map? ?? {});
    final away = Map<String, dynamic>.from(match!['away'] as Map? ?? {});
    final events = (match!['events'] as List? ?? []);
    final organizer = widget.api.role == 'league_organizer' || widget.api.role == 'super_admin';
    final homeName = '${home['team_id'] ?? 'Home'}';
    final awayName = '${away['team_id'] ?? 'Away'}';
    final scoring = (match!['scoring_events'] as List? ?? []).map((item) => '$item').where((item) => item.isNotEmpty).toList();
    final eventNames = scoring.isEmpty ? const ['goal'] : scoring;
    final teamId = scoringSide == 'home' ? homeName : awayName;
    return Scaffold(
      appBar: AppBar(title: Text('${_pretty(homeName)} vs ${_pretty(awayName)}')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 28),
        children: [
          _Scoreboard(
            home: homeName,
            away: awayName,
            homeScore: '${home['score'] ?? 0}',
            awayScore: '${away['score'] ?? 0}',
            status: '${match!['status']}',
            outcome: _words('${match!['outcome']}'),
          ),
          if (match!['pinned_variant_id'] != null) ...[
            const SizedBox(height: 12),
            _Panel(
              child: ListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 14),
                leading: const Icon(Icons.push_pin),
                title: const Text('Pinned product', style: TextStyle(fontWeight: FontWeight.w800)),
                subtitle: Text('${match!['pinned_variant_id']}'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () {
                  widget.api.highlight = '${match!['pinned_variant_id']}';
                  Navigator.pop(context);
                  widget.onOpenStore();
                },
              ),
            ),
          ],
          const SizedBox(height: 20),
          const _Section('Timeline'),
          const SizedBox(height: 10),
          if (events.isEmpty) const _Panel(child: Padding(padding: EdgeInsets.all(16), child: Text('No events yet'))),
          for (var i = 0; i < events.length; i++)
            _TimelineTile(
              title: _eventTitle(events[i]),
              detail: _eventDetail(events[i]),
              corrected: events[i]['compensated'] == true,
              last: i == events.length - 1,
            ),
          if (message != null) ...[const SizedBox(height: 12), _Note(text: message!)],
          if (organizer) ...[
            const SizedBox(height: 16),
            const _Section('Record a score'),
            const SizedBox(height: 8),
            SegmentedButton<String>(
              segments: [
                ButtonSegment(value: 'home', label: Text(_pretty(homeName))),
                ButtonSegment(value: 'away', label: Text(_pretty(awayName))),
              ],
              selected: {scoringSide},
              onSelectionChanged: (value) => setState(() => scoringSide = value.first),
            ),
            const SizedBox(height: 8),
            for (final event in eventNames) ...[
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: () => _score(teamId, event),
                  child: Text('${_pretty(event)} for ${_pretty(teamId)}'),
                ),
              ),
              const SizedBox(height: 8),
            ],
            SizedBox(width: double.infinity, child: OutlinedButton(onPressed: _compensate, child: const Text('Correct last score'))),
          ],
        ],
      ),
    );
  }
}

class StandingsPage extends StatefulWidget {
  const StandingsPage({super.key, required this.api, required this.onQueued, required this.onOpenTab});
  final Api api;
  final Future<void> Function() onQueued;
  final void Function(int index) onOpenTab;

  @override
  State<StandingsPage> createState() => _StandingsPageState();
}

class _StandingsPageState extends State<StandingsPage> {
  List<dynamic> table = [];
  List<dynamic> fixtures = [];
  List<String> leagues = ['demo-league'];
  String leagueId = 'demo-league';
  bool loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final listed = await widget.api.send('GET', '/api/v1/fixtures');
      fixtures = listed['fixtures'] as List? ?? [];
      final found = <String>{};
      for (final raw in fixtures) {
        final id = '${(raw as Map)['league_id'] ?? ''}';
        if (id.isNotEmpty) found.add(id);
      }
      if (found.isNotEmpty) leagues = found.toList()..sort();
    } on ApiException {
      final cached = await widget.api.local.readMap('fixtures');
      fixtures = cached?['fixtures'] as List? ?? [];
    }
    try {
      final data = await widget.api.send('GET', '/api/v1/standings/$leagueId');
      table = data['table'] as List? ?? [];
      await widget.api.local.save('standings-$leagueId', data);
    } on ApiException {
      final cached = await widget.api.local.readMap('standings-$leagueId') ?? await widget.api.local.readMap('standings');
      table = cached?['table'] as List? ?? [];
    }
    if (mounted) setState(() => loading = false);
  }

  void _openTeam(String teamId) {
    final page = context;
    final matches = fixtures.where((raw) {
      final row = raw as Map;
      return row['home'] == teamId || row['away'] == teamId;
    }).toList();
    showModalBottomSheet<void>(
      context: page,
      showDragHandle: true,
      builder: (context) => ListView(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
        children: [
          Text(_pretty(teamId), style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
          const SizedBox(height: 8),
          if (matches.isEmpty) const Text('No matches for this team.'),
          for (final raw in matches)
            Builder(builder: (context) {
              final row = Map<String, dynamic>.from(raw as Map);
              return ListTile(
                title: Text('${_pretty('${row['home']}')} vs ${_pretty('${row['away']}')}'),
                subtitle: Text(_words('${row['status']}')),
                onTap: () {
                  final matchId = row['match_id'] as String;
                  Navigator.pop(context);
                  Navigator.push(
                    page,
                    MaterialPageRoute(
                      builder: (_) => MatchPage(
                        api: widget.api,
                        matchId: matchId,
                        onQueued: widget.onQueued,
                        onOpenStore: () => widget.onOpenTab(2),
                      ),
                    ),
                  );
                },
              );
            }),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (loading) return const _LoadingList();
    if (table.isEmpty) return const Center(child: Text('No matches yet'));
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(16),
        children: [
          SizedBox(
            height: 44,
            child: ListView(
              scrollDirection: Axis.horizontal,
              children: [
                for (final id in leagues)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: FilterChip(
                      label: Text(_pretty(id)),
                      selected: leagueId == id,
                      onSelected: (_) {
                        setState(() {
                          leagueId = id;
                          loading = true;
                        });
                        _load();
                      },
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          _Panel(
            child: Column(
              children: [
                const Padding(
                  padding: EdgeInsets.fromLTRB(14, 14, 14, 8),
                  child: Row(
                    children: [
                      SizedBox(width: 36, child: Text('#', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w800))),
                      Expanded(child: Text('Team', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w800))),
                      _Col('W', 22),
                      _Col('D', 22),
                      _Col('L', 22),
                      _Col('Pts', 32),
                    ],
                  ),
                ),
                const Divider(height: 1),
                for (final raw in table)
                  Builder(builder: (context) {
                    final row = Map<String, dynamic>.from(raw as Map);
                    final rank = row['rank'];
                    final diff = row['diff'];
                    final diffText = diff is num && diff > 0 ? '+$diff' : '$diff';
                    return InkWell(
                      onTap: () => _openTeam('${row['team_id']}'),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                        child: Row(
                          children: [
                            SizedBox(
                              width: 36,
                              child: CircleAvatar(
                                radius: 12,
                                backgroundColor: rank == 1 ? Theme.of(context).colorScheme.secondary : Theme.of(context).colorScheme.surfaceContainerHighest,
                                child: Text('$rank', style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w800)),
                              ),
                            ),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(_pretty('${row['team_id']}'), style: const TextStyle(fontWeight: FontWeight.w800)),
                                  Text(
                                    'P ${row['played']} · ${row['scored']}-${row['conceded']} · GD $diffText',
                                    style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant),
                                  ),
                                  Text(_words('${row['status']}'), style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.onSurfaceVariant)),
                                ],
                              ),
                            ),
                            _Col('${row['won']}', 22),
                            _Col('${row['drawn']}', 22),
                            _Col('${row['lost']}', 22),
                            _Col('${row['points']}', 32, strong: true),
                          ],
                        ),
                      ),
                    );
                  }),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class StorePage extends StatefulWidget {
  const StorePage({super.key, required this.api});
  final Api api;

  @override
  State<StorePage> createState() => _StorePageState();
}

class _StorePageState extends State<StorePage> {
  List<dynamic> products = [];
  final List<Map<String, dynamic>> cart = [];
  Map<String, dynamic>? lastOrder;
  String? message;
  bool loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final cached = await widget.api.local.readMap('products');
    if (cached != null) {
      products = cached['products'] as List? ?? products;
    }
    try {
      final data = await widget.api.send('GET', '/api/v1/products');
      products = data['products'] as List? ?? [];
      await widget.api.local.save('products', data);
      message = null;
    } on ApiException catch (caught) {
      message = products.isEmpty ? caught.message : null;
    }
    if (mounted) setState(() => loading = false);
  }

  void add(Map<String, dynamic> variant, String productName) {
    final id = '${variant['variant_id']}';
    final stock = variant['stock'] is num ? (variant['stock'] as num).toInt() : 0;
    if (stock < 1) {
      setState(() => message = 'That size is sold out.');
      return;
    }
    final existing = cart.where((row) => row['variant_id'] == id);
    if (existing.isNotEmpty) {
      final row = existing.first;
      if ((row['qty'] as int) >= stock) {
        setState(() => message = 'Only $stock left.');
        return;
      }
      setState(() {
        row['qty'] = (row['qty'] as int) + 1;
        message = 'Quantity updated.';
      });
      return;
    }
    setState(() {
      cart.add({
        'variant_id': id,
        'name': productName,
        'size': variant['size'],
        'color': variant['color'],
        'price_minor': variant['price_minor'],
        'stock': stock,
        'qty': 1,
      });
      message = 'Added to cart.';
    });
  }

  Future<void> _openCart() async {
    final order = await Navigator.push<Map<String, dynamic>>(
      context,
      MaterialPageRoute(builder: (_) => CartPage(api: widget.api, cart: cart)),
    );
    if (!mounted || order == null) {
      setState(() {});
      return;
    }
    cart.clear();
    setState(() {
      lastOrder = order;
      message = 'Payment captured. Receipt is ready.';
    });
    if (!mounted) return;
    await Navigator.push(context, MaterialPageRoute(builder: (_) => ReceiptPage(api: widget.api, order: order)));
  }

  @override
  Widget build(BuildContext context) {
    if (loading && products.isEmpty) return const _LoadingList();
    if (products.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: _Panel(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(Icons.storefront, size: 36),
                  const SizedBox(height: 12),
                  Text(message ?? 'No products yet', textAlign: TextAlign.center),
                  const SizedBox(height: 16),
                  FilledButton(onPressed: () { setState(() => loading = true); _load(); }, child: const Text('Try again')),
                ],
              ),
            ),
          ),
        ),
      );
    }
    final count = cart.fold<int>(0, (sum, row) => sum + (row['qty'] as int));
    return Column(
      children: [
        Expanded(
          child: RefreshIndicator(
            onRefresh: _load,
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(16),
              children: [
                if (message != null) ...[_Note(text: message!), const SizedBox(height: 10)],
                if (lastOrder != null) ...[
                  _Panel(
                    child: ListTile(
                      leading: const Icon(Icons.receipt_long),
                      title: const Text('Latest receipt', style: TextStyle(fontWeight: FontWeight.w800)),
                      subtitle: Text('${lastOrder!['order_id']} · PKR ${_money(lastOrder!['total_minor'])}'),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => ReceiptPage(api: widget.api, order: lastOrder!))),
                    ),
                  ),
                  const SizedBox(height: 10),
                ],
                for (final raw in products) ...[
                  _ProductCard(
                    product: Map<String, dynamic>.from(raw as Map),
                    highlight: widget.api.highlight,
                    onAdd: add,
                  ),
                  const SizedBox(height: 12),
                ],
              ],
            ),
          ),
        ),
        if (cart.isNotEmpty)
          Material(
            elevation: 8,
            child: SafeArea(
              top: false,
              child: ListTile(
                title: Text('$count in cart', style: const TextStyle(fontWeight: FontWeight.w800)),
                trailing: FilledButton(onPressed: _openCart, child: const Text('Checkout')),
              ),
            ),
          ),
      ],
    );
  }
}

class _ProductCard extends StatelessWidget {
  const _ProductCard({required this.product, required this.onAdd, this.highlight});
  final Map<String, dynamic> product;
  final String? highlight;
  final void Function(Map<String, dynamic> variant, String productName) onAdd;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final variants = (product['variants'] as List? ?? []);
    return _Panel(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('${product['name']}', style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
            const SizedBox(height: 4),
            Text('${product['category']}', style: TextStyle(color: scheme.onSurfaceVariant)),
            if ('${product['description'] ?? ''}'.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text('${product['description']}'),
            ],
            for (final raw in variants)
              Builder(builder: (context) {
                final variant = Map<String, dynamic>.from(raw as Map);
                final stock = variant['stock'] is num ? variant['stock'] as num : 0;
                final price = variant['price_minor'] is num ? (variant['price_minor'] as num) / 100 : 0;
                final soldOut = stock == 0;
                final marked = highlight != null && highlight == '${variant['variant_id']}';
                return Padding(
                  padding: const EdgeInsets.only(top: 14),
                  child: Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: marked ? scheme.primary : scheme.outlineVariant),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Expanded(child: Text('Size ${variant['size']} · ${variant['color'] ?? 'one color'}')),
                            Text('PKR ${price.toStringAsFixed(0)}', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
                          ],
                        ),
                        const SizedBox(height: 4),
                        Text(soldOut ? 'Sold out' : '$stock in stock', style: TextStyle(color: scheme.onSurfaceVariant)),
                        const SizedBox(height: 8),
                        SizedBox(
                          width: double.infinity,
                          child: FilledButton(
                            onPressed: soldOut ? null : () => onAdd(variant, '${product['name']}'),
                            child: Text(soldOut ? 'Sold out' : 'Add to cart'),
                          ),
                        ),
                      ],
                    ),
                  ),
                );
              }),
          ],
        ),
      ),
    );
  }
}

class CartPage extends StatefulWidget {
  const CartPage({super.key, required this.api, required this.cart});
  final Api api;
  final List<Map<String, dynamic>> cart;

  @override
  State<CartPage> createState() => _CartPageState();
}

class _CartPageState extends State<CartPage> {
  final discount = TextEditingController(text: 'WELCOME10');
  String payment = 'tok_success';
  String? message;
  bool busy = false;

  @override
  void dispose() {
    discount.dispose();
    super.dispose();
  }

  int get _subtotal => widget.cart.fold<int>(0, (sum, row) {
        final price = row['price_minor'] is num ? (row['price_minor'] as num).toInt() : 0;
        return sum + price * (row['qty'] as int);
      });

  int get _discount {
    final code = discount.text.trim().toUpperCase();
    return code == 'WELCOME10' ? (_subtotal * 10) ~/ 100 : 0;
  }

  Future<void> _pay() async {
    if (widget.cart.isEmpty) return;
    setState(() {
      busy = true;
      message = null;
    });
    final code = discount.text.trim();
    try {
      final data = await widget.api.send('POST', '/api/v1/checkout', {
        'order_id': 'ord-${DateTime.now().millisecondsSinceEpoch}',
        'customer_id': 'phone-user',
        'items': [
          for (final row in widget.cart) {'variant_id': row['variant_id'], 'quantity': row['qty']},
        ],
        'discount_code': code.isEmpty ? null : code,
        'payment': {'token': payment},
      });
      if (!mounted) return;
      Navigator.pop(context, data);
    } on ApiException catch (caught) {
      setState(() {
        busy = false;
        message = caught.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Cart')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (widget.cart.isEmpty) const Text('Your cart is empty.'),
          for (final row in widget.cart) ...[
            _Panel(
              child: ListTile(
                title: Text('${row['name']}', style: const TextStyle(fontWeight: FontWeight.w800)),
                subtitle: Text('Size ${row['size']} · ${row['color']} · PKR ${_money(row['price_minor'])}'),
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    IconButton(
                      onPressed: () => setState(() {
                        final qty = row['qty'] as int;
                        if (qty <= 1) {
                          widget.cart.remove(row);
                        } else {
                          row['qty'] = qty - 1;
                        }
                      }),
                      icon: const Icon(Icons.remove),
                    ),
                    Text('${row['qty']}', style: const TextStyle(fontWeight: FontWeight.w800)),
                    IconButton(
                      onPressed: () => setState(() {
                        final qty = row['qty'] as int;
                        final stock = row['stock'] is num ? (row['stock'] as num).toInt() : qty;
                        if (qty < stock) row['qty'] = qty + 1;
                      }),
                      icon: const Icon(Icons.add),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 8),
          ],
          const SizedBox(height: 8),
          TextField(
            controller: discount,
            decoration: const InputDecoration(labelText: 'Discount code', border: OutlineInputBorder()),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            initialValue: payment,
            decoration: const InputDecoration(labelText: 'Mock payment', border: OutlineInputBorder()),
            items: const [
              DropdownMenuItem(value: 'tok_success', child: Text('Successful payment')),
              DropdownMenuItem(value: 'tok_declined', child: Text('Declined card')),
              DropdownMenuItem(value: 'tok_capture_fail', child: Text('Capture fails')),
            ],
            onChanged: (value) => setState(() => payment = value ?? 'tok_success'),
          ),
          const SizedBox(height: 16),
          _Panel(
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                children: [
                  _MoneyRow('Subtotal', _subtotal),
                  _MoneyRow('Discount', _discount),
                  _MoneyRow('Total', _subtotal - _discount, strong: true),
                ],
              ),
            ),
          ),
          if (message != null) ...[const SizedBox(height: 12), _Note(text: message!, error: true)],
          const SizedBox(height: 16),
          FilledButton(
            onPressed: busy || widget.cart.isEmpty ? null : _pay,
            child: Text(busy ? 'Paying…' : 'Place order'),
          ),
        ],
      ),
    );
  }
}

class _MoneyRow extends StatelessWidget {
  const _MoneyRow(this.label, this.minor, {this.strong = false});
  final String label;
  final int minor;
  final bool strong;

  @override
  Widget build(BuildContext context) {
    final style = TextStyle(fontWeight: strong ? FontWeight.w800 : FontWeight.w500, fontSize: strong ? 18 : 14);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Expanded(child: Text(label, style: style)),
          Text('PKR ${_money(minor)}', style: style),
        ],
      ),
    );
  }
}

class ReceiptPage extends StatelessWidget {
  const ReceiptPage({super.key, required this.api, required this.order});
  final Api api;
  final Map<String, dynamic> order;

  Future<void> _save(BuildContext context) async {
    final pdf = order['receipt'] is Map ? order['receipt']['pdf_base64'] : null;
    if (pdf is! String || pdf.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('This receipt has no PDF.')));
      return;
    }
    try {
      await api.local.savePdf('${order['order_id']}', pdf);
    } catch (_) {
      if (!context.mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('The PDF could not be saved.')));
      return;
    }
    if (!context.mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('PDF saved on this phone.')));
  }

  @override
  Widget build(BuildContext context) {
    final items = (order['items'] as List? ?? []);
    return Scaffold(
      appBar: AppBar(title: const Text('Receipt')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _Panel(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('${order['order_id']}', style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
                  const SizedBox(height: 4),
                  Text(_words('${order['status']}')),
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          const _Section('Items'),
          const SizedBox(height: 8),
          for (final raw in items)
            Builder(builder: (context) {
              final item = Map<String, dynamic>.from(raw as Map);
              return ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text('${item['variant_id']}'),
                subtitle: Text('Qty ${item['quantity']} · PKR ${_money(item['unit_price_minor'])}'),
              );
            }),
          _MoneyRow('Subtotal', (order['subtotal_minor'] as num?)?.toInt() ?? 0),
          _MoneyRow('Discount', (order['discount_minor'] as num?)?.toInt() ?? 0),
          _MoneyRow('Total', (order['total_minor'] as num?)?.toInt() ?? 0, strong: true),
          const SizedBox(height: 16),
          FilledButton.icon(onPressed: () => _save(context), icon: const Icon(Icons.download), label: const Text('Download PDF')),
          const SizedBox(height: 8),
          OutlinedButton(onPressed: () => Navigator.pop(context), child: const Text('Back to store')),
        ],
      ),
    );
  }
}

class ClubPage extends StatefulWidget {
  const ClubPage({super.key, required this.api, required this.onQueued});
  final Api api;
  final Future<void> Function() onQueued;

  @override
  State<ClubPage> createState() => _ClubPageState();
}

class _ClubPageState extends State<ClubPage> {
  Map<String, dynamic>? club;
  String? message;
  bool loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      club = await widget.api.send('GET', '/api/v1/clubs/club-alpha-1');
      await widget.api.local.save('club', club!);
    } on ApiException catch (caught) {
      club = await widget.api.local.readMap('club');
      message = club == null ? caught.message : 'Showing the saved club.';
    }
    if (mounted) setState(() => loading = false);
  }

  Future<void> _history(String playerId, String name) async {
    try {
      final data = await widget.api.send('GET', '/audit/v1/roster/history/$playerId');
      final rows = (data['history'] as List? ?? []);
      if (!mounted) return;
      await showModalBottomSheet<void>(
        context: context,
        showDragHandle: true,
        builder: (context) => ListView(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
          children: [
            Text('$name history', style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
            const SizedBox(height: 8),
            if (rows.isEmpty) const Text('No moves recorded yet.'),
            for (final raw in rows)
              Builder(builder: (context) {
                final row = Map<String, dynamic>.from(raw as Map);
                final action = _words('${row['action'] ?? row['state'] ?? 'move'}');
                return ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(action, style: const TextStyle(fontWeight: FontWeight.w700)),
                  subtitle: Text('${row['from_squad_id'] ?? '—'} → ${row['to_squad_id'] ?? 'released'} · ${row['effective_date'] ?? row['when'] ?? ''}'),
                );
              }),
          ],
        ),
      );
    } on ApiException catch (caught) {
      setState(() => message = caught.message);
    }
  }

  Future<void> _manage(Map<String, dynamic> player) async {
    final squads = <String>{};
    for (final raw in (club?['roster'] as List? ?? [])) {
      final id = '${(raw as Map)['squad_id'] ?? ''}';
      if (id.isNotEmpty && id != 'null') squads.add(id);
    }
    final from = '${player['squad_id'] ?? ''}';
    final other = squads.where((id) => id != from);
    var kind = 'transfer';
    var day = DateTime.now().toIso8601String().split('T').first;
    var loanEnd = DateTime.now().add(const Duration(days: 30)).toIso8601String().split('T').first;
    final destination = TextEditingController(text: other.isEmpty ? '' : other.first);
    final reason = TextEditingController();
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) => Padding(
        padding: EdgeInsets.fromLTRB(16, 0, 16, 16 + MediaQuery.viewInsetsOf(context).bottom),
        child: SingleChildScrollView(
        child: StatefulBuilder(
          builder: (context, setSheet) => Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('${player['name']}', style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800)),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                initialValue: kind,
                decoration: const InputDecoration(labelText: 'Action', border: OutlineInputBorder()),
                items: const [
                  DropdownMenuItem(value: 'transfer', child: Text('Transfer')),
                  DropdownMenuItem(value: 'loan', child: Text('Loan')),
                  DropdownMenuItem(value: 'release', child: Text('Release')),
                ],
                onChanged: (value) => setSheet(() => kind = value ?? 'transfer'),
              ),
              if (kind != 'release') ...[
                const SizedBox(height: 12),
                TextField(controller: destination, decoration: const InputDecoration(labelText: 'Destination squad', border: OutlineInputBorder())),
              ],
              const SizedBox(height: 12),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Effective date'),
                subtitle: Text(day),
                onTap: () async {
                  final picked = await showDatePicker(context: context, initialDate: DateTime.tryParse(day) ?? DateTime.now(), firstDate: DateTime(2026), lastDate: DateTime(2027, 12, 31));
                  if (picked != null) setSheet(() => day = picked.toIso8601String().split('T').first);
                },
              ),
              if (kind == 'loan')
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Loan ends'),
                  subtitle: Text(loanEnd),
                  onTap: () async {
                    final picked = await showDatePicker(context: context, initialDate: DateTime.tryParse(loanEnd) ?? DateTime.now(), firstDate: DateTime(2026), lastDate: DateTime(2027, 12, 31));
                    if (picked != null) setSheet(() => loanEnd = picked.toIso8601String().split('T').first);
                  },
                ),
              TextField(controller: reason, decoration: const InputDecoration(labelText: 'Reason', border: OutlineInputBorder())),
              const SizedBox(height: 12),
              SizedBox(
                width: double.infinity,
                child: FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Review')),
              ),
            ],
          ),
        ),
        ),
      ),
    );
    final to = destination.text.trim();
    final why = reason.text.trim();
    destination.dispose();
    reason.dispose();
    if (saved != true || !mounted) return;
    if (why.isEmpty) {
      setState(() => message = 'Add a reason before you continue.');
      return;
    }
    final confirm = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('${_pretty(kind)} ${player['name']}?'),
        content: Text(kind == 'release' ? 'This releases the player on $day.' : 'This $kind runs on $day. Offline changes stay on this phone until sync.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Confirm')),
        ],
      ),
    );
    if (confirm != true) return;
    final body = {
      'request_id': 'tr-${DateTime.now().millisecondsSinceEpoch}',
      'league_id': '${player['league_id'] ?? 'demo-roster'}',
      'player_id': player['player_id'],
      'type': kind,
      'from_squad_id': from,
      'to_squad_id': kind == 'release' ? null : to,
      'effective_date': day,
      'loan_end_date': kind == 'loan' ? loanEnd : null,
      'reason': why,
      'requested_by': 'manager',
    };
    final action = {
      'action_id': body['request_id'],
      'type': 'roster_transfer',
      'queued_at': '${DateTime.now().toUtc().toIso8601String().split('.').first}Z',
      'base_version': 0,
      'payload': body,
    };
    final label = '${_pretty(kind)} ${player['name']}';
    if (widget.api.link == 'Offline') {
      await widget.api.local.enqueue(action, label);
      await widget.onQueued();
      setState(() => message = '$label saved on this phone.');
      return;
    }
    try {
      await widget.api.send('POST', '/api/v1/roster/transfer', body);
      setState(() => message = '$label recorded.');
      await _load();
    } on ApiException catch (caught) {
      await widget.api.local.enqueue(action, label);
      await widget.onQueued();
      setState(() => message = caught.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (loading && club == null) return const _LoadingList();
    if (club == null) return Center(child: Text(message ?? 'Club could not be loaded.'));
    final staff = Map<String, dynamic>.from(club!['staff'] as Map? ?? {});
    final venue = Map<String, dynamic>.from(club!['venue'] as Map? ?? {});
    final socials = club!['socials'] is Map ? Map<String, dynamic>.from(club!['socials'] as Map) : <String, dynamic>{};
    final roster = (club!['roster'] as List? ?? []);
    final manager = widget.api.role == 'club_manager' || widget.api.role == 'super_admin';
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        _Panel(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    _Crest('${club!['name']}', home: true, radius: 26),
                    const SizedBox(width: 12),
                    Expanded(child: Text(club!['name'] as String, style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800))),
                  ],
                ),
                const SizedBox(height: 6),
                Row(
                  children: [
                    Icon(Icons.stadium, size: 16, color: Theme.of(context).colorScheme.onSurfaceVariant),
                    const SizedBox(width: 6),
                    Expanded(child: Text('${venue['name']}', style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant))),
                  ],
                ),
                if (socials.isNotEmpty) ...[
                  const SizedBox(height: 6),
                  Text(socials.entries.map((entry) => '${entry.key}: ${entry.value}').join(' · '), style: TextStyle(color: Theme.of(context).colorScheme.onSurfaceVariant)),
                ],
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    _StaffChip('Coach', staff['coach']),
                    _StaffChip('Manager', staff['manager']),
                    _StaffChip('Physio', staff['physio']),
                  ],
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 16),
        const _Section('Roster'),
        const SizedBox(height: 8),
        if (roster.isEmpty) const Text('No players yet'),
        for (final raw in roster) ...[
          Builder(builder: (context) {
            final player = Map<String, dynamic>.from(raw as Map);
            final canMove = manager && '${player['squad_id'] ?? ''}'.isNotEmpty && player['squad_id'] != null;
            return _Panel(
              child: ListTile(
                leading: _Crest('${player['name']}', home: true, radius: 18),
                title: Text('${player['name']}', style: const TextStyle(fontWeight: FontWeight.w800)),
                subtitle: Text(_playerLine(player)),
                trailing: canMove
                    ? IconButton(onPressed: () => _manage(player), icon: const Icon(Icons.swap_horiz), tooltip: 'Transfer, loan, or release')
                    : IconButton(onPressed: () => _history('${player['player_id']}', '${player['name']}'), icon: const Icon(Icons.history), tooltip: 'History'),
                onTap: () => _history('${player['player_id']}', '${player['name']}'),
              ),
            );
          }),
          const SizedBox(height: 8),
        ],
        if (manager) const _Note(text: 'Tap the arrows to transfer, loan, or release a player. New players are registered by the league office.'),
        if (message != null) ...[const SizedBox(height: 4), _Note(text: message!)],
      ],
    );
  }

}

Color _color(Object? value, [Color fallback = const Color(0xFF0B3D2E)]) {
  if (value is! String || !value.startsWith('#') || value.length != 7) return fallback;
  return Color(int.parse(value.substring(1), radix: 16) + 0xFF000000);
}

IconData _linkIcon(String link) {
  if (link == 'Offline') return Icons.cloud_off;
  if (link == 'Syncing') return Icons.sync;
  return Icons.cloud_done;
}

class MorePage extends StatelessWidget {
  const MorePage({super.key, required this.api, required this.queue, required this.reports, required this.onSync, required this.onLogout, required this.onChanged});
  final Api api;
  final List<Map<String, dynamic>> queue;
  final List<String> reports;
  final VoidCallback onSync;
  final VoidCallback onLogout;
  final VoidCallback onChanged;

  Future<void> _editServer(BuildContext context) async {
    final controller = TextEditingController(text: api.base);
    final next = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Server address'),
        content: TextField(controller: controller, decoration: const InputDecoration(labelText: 'Address', border: OutlineInputBorder())),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, controller.text.trim()), child: const Text('Save')),
        ],
      ),
    );
    controller.dispose();
    if (next == null || next.isEmpty) return;
    await api.saveBase(next);
    onChanged();
  }

  Future<void> _signOut(BuildContext context) async {
    final leave = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Sign out?'),
        content: Text(queue.isEmpty ? 'You can sign in again with a demo account.' : 'You still have ${queue.length} action(s) on this phone. They stay until this phone syncs.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Stay')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Sign out')),
        ],
      ),
    );
    if (leave == true) {
      await api.logout();
      onLogout();
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final role = _pretty(api.role.replaceAll('_', ' '));
    final initial = api.name.isEmpty ? 'N' : api.name[0].toUpperCase();
    final quiet = queue.isEmpty && reports.isEmpty;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 28),
      children: [
        Container(
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
            color: scheme.primary,
            borderRadius: BorderRadius.circular(22),
          ),
          child: Row(
            children: [
              Container(
                padding: const EdgeInsets.all(3),
                decoration: BoxDecoration(color: scheme.secondary, shape: BoxShape.circle),
                child: CircleAvatar(
                  radius: 28,
                  backgroundColor: Colors.white,
                  child: Text(initial, style: TextStyle(color: scheme.primary, fontSize: 22, fontWeight: FontWeight.w800)),
                ),
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(api.name.isEmpty ? 'Signed in' : api.name, style: const TextStyle(color: Colors.white, fontSize: 22, fontWeight: FontWeight.w800)),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        _LightPill(role.isEmpty ? 'Member' : role),
                        _LightPill(api.tenantName.isEmpty ? 'NaSCon League' : api.tenantName),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 14),
        _Panel(
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 14, 16, 12),
                child: Row(
                  children: [
                    Icon(_linkIcon(api.link), color: scheme.primary, size: 20),
                    const SizedBox(width: 8),
                    Text(api.link, style: const TextStyle(fontWeight: FontWeight.w800)),
                    const Spacer(),
                    Flexible(child: Text(api.base, textAlign: TextAlign.end, overflow: TextOverflow.ellipsis, style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 12))),
                  ],
                ),
              ),
              const Divider(height: 1),
              ListTile(
                leading: Icon(Icons.sync, color: scheme.primary),
                title: const Text('Sync now', style: TextStyle(fontWeight: FontWeight.w700)),
                subtitle: const Text('Send saved changes to the server'),
                trailing: const Icon(Icons.chevron_right),
                onTap: onSync,
              ),
              const Divider(height: 1),
              ListTile(
                leading: Icon(Icons.dns_outlined, color: scheme.primary),
                title: const Text('Server address', style: TextStyle(fontWeight: FontWeight.w700)),
                subtitle: const Text('Used by this phone'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => _editServer(context),
              ),
            ],
          ),
        ),
        const SizedBox(height: 22),
        const _Section('Activity'),
        const SizedBox(height: 8),
        if (quiet)
          _Panel(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 28),
              child: Column(
                children: [
                  Icon(Icons.verified_outlined, color: scheme.primary, size: 28),
                  const SizedBox(height: 10),
                  const Text('All caught up', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w800)),
                  const SizedBox(height: 4),
                  Text('Offline scores and transfers show here after sync.', textAlign: TextAlign.center, style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 13)),
                ],
              ),
            ),
          )
        else
          _Panel(
            child: Column(
              children: [
                for (final row in queue)
                  ListTile(
                    leading: Icon(Icons.schedule, color: scheme.secondary),
                    title: Text('${row['label']}', style: const TextStyle(fontWeight: FontWeight.w700)),
                    subtitle: const Text('Waiting on this phone'),
                  ),
                for (final line in reports.take(12))
                  ListTile(
                    leading: Icon(Icons.fact_check_outlined, color: scheme.primary),
                    title: Text(line, style: const TextStyle(fontWeight: FontWeight.w600)),
                  ),
              ],
            ),
          ),
        const SizedBox(height: 18),
        Center(
          child: TextButton.icon(
            onPressed: () => _signOut(context),
            style: TextButton.styleFrom(foregroundColor: scheme.error),
            icon: const Icon(Icons.logout),
            label: const Text('Sign out'),
          ),
        ),
      ],
    );
  }
}

class _LightPill extends StatelessWidget {
  const _LightPill(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: Colors.white.withValues(alpha: 0.16), borderRadius: BorderRadius.circular(20)),
      child: Text(text, style: const TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w700)),
    );
  }
}

class _SyncChip extends StatelessWidget {
  const _SyncChip({required this.link, required this.onTap});
  final String link;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final primary = Theme.of(context).colorScheme.primary;
    return Padding(
      padding: const EdgeInsets.only(right: 12),
      child: Tooltip(
        message: '$link. Tap to sync',
        child: Material(
          color: Colors.white,
          borderRadius: BorderRadius.circular(20),
          child: InkWell(
            onTap: onTap,
            borderRadius: BorderRadius.circular(20),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(link == 'Syncing' ? Icons.sync : _linkIcon(link), size: 16, color: primary),
                  const SizedBox(width: 4),
                  Text(link, style: TextStyle(color: primary, fontWeight: FontWeight.w800, fontSize: 12)),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _LoadingList extends StatelessWidget {
  const _LoadingList();

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.surfaceContainerHighest;
    return ListView(
      physics: const NeverScrollableScrollPhysics(),
      padding: const EdgeInsets.all(16),
      children: [
        for (var i = 0; i < 4; i++) ...[
          Container(height: 92, decoration: BoxDecoration(color: color, borderRadius: BorderRadius.circular(16))),
          const SizedBox(height: 10),
        ],
      ],
    );
  }
}

class _Section extends StatelessWidget {
  const _Section(this.text);
  final String text;

  @override
  Widget build(BuildContext context) {
    return Text(text, style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800));
  }
}

class _Note extends StatelessWidget {
  const _Note({required this.text, this.error = false});
  final String text;
  final bool error;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: error ? scheme.errorContainer : scheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(text, style: TextStyle(color: error ? scheme.onErrorContainer : scheme.onSurface)),
    );
  }
}

class _FixtureCard extends StatelessWidget {
  const _FixtureCard({
    required this.status,
    required this.when,
    required this.home,
    required this.away,
    required this.homeScore,
    required this.awayScore,
    required this.onTap,
  });

  final String status;
  final String when;
  final String home;
  final String away;
  final String homeScore;
  final String awayScore;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return _Panel(
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 14, 8),
          child: Column(
            children: [
              Row(
                children: [
                  _StatusPill(status),
                  const Spacer(),
                  Icon(Icons.schedule, size: 14, color: scheme.onSurfaceVariant),
                  const SizedBox(width: 4),
                  Text(when, style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 12, fontWeight: FontWeight.w600)),
                ],
              ),
              const SizedBox(height: 8),
              _TeamScoreRow(name: home, score: homeScore, home: true),
              Divider(height: 1, color: scheme.outlineVariant.withValues(alpha: 0.5)),
              _TeamScoreRow(name: away, score: awayScore, home: false),
            ],
          ),
        ),
      ),
    );
  }
}

class _TeamScoreRow extends StatelessWidget {
  const _TeamScoreRow({required this.name, required this.score, required this.home});
  final String name;
  final String score;
  final bool home;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 10),
      child: Row(
        children: [
          _Crest(name, home: home),
          const SizedBox(width: 10),
          Expanded(child: Text(_pretty(name), style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800))),
          Text(score, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
        ],
      ),
    );
  }
}

class _Scoreboard extends StatelessWidget {
  const _Scoreboard({
    required this.home,
    required this.away,
    required this.homeScore,
    required this.awayScore,
    required this.status,
    required this.outcome,
  });

  final String home;
  final String away;
  final String homeScore;
  final String awayScore;
  final String status;
  final String outcome;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return _Panel(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 18, 16, 18),
        child: Column(
          children: [
            _StatusPill(status),
            const SizedBox(height: 16),
            Row(
              children: [
                Expanded(child: _Side(name: home, home: true)),
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 8),
                  child: Text('$homeScore  –  $awayScore', style: const TextStyle(fontSize: 32, fontWeight: FontWeight.w800, letterSpacing: -0.5)),
                ),
                Expanded(child: _Side(name: away, home: false)),
              ],
            ),
            if (outcome.isNotEmpty) ...[
              const SizedBox(height: 10),
              Text(outcome, style: TextStyle(color: scheme.onSurfaceVariant, fontWeight: FontWeight.w600)),
            ],
          ],
        ),
      ),
    );
  }
}

class _Side extends StatelessWidget {
  const _Side({required this.name, required this.home});
  final String name;
  final bool home;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        _Crest(name, home: home, radius: 22),
        const SizedBox(height: 8),
        Text(_pretty(name), textAlign: TextAlign.center, style: const TextStyle(fontWeight: FontWeight.w800)),
      ],
    );
  }
}

class _TimelineTile extends StatelessWidget {
  const _TimelineTile({required this.title, required this.detail, required this.corrected, required this.last});
  final String title;
  final String detail;
  final bool corrected;
  final bool last;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            width: 22,
            child: Column(
              children: [
                Container(
                  width: 12,
                  height: 12,
                  margin: const EdgeInsets.only(top: 16),
                  decoration: BoxDecoration(color: corrected ? scheme.error : scheme.primary, shape: BoxShape.circle),
                ),
                if (!last) Expanded(child: Container(width: 2, color: scheme.outlineVariant)),
              ],
            ),
          ),
          Expanded(
            child: Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: _Panel(
                child: ListTile(
                  title: Text(title, style: const TextStyle(fontWeight: FontWeight.w800)),
                  subtitle: detail.isEmpty ? null : Text(detail),
                  trailing: corrected ? const _StatusPill('corrected') : null,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Panel extends StatelessWidget {
  const _Panel({required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: dark ? const Color(0xFF1B2420) : Colors.white,
        borderRadius: BorderRadius.circular(18),
        boxShadow: [
          BoxShadow(color: Colors.black.withValues(alpha: dark ? 0.25 : 0.06), blurRadius: 16, offset: const Offset(0, 6)),
        ],
      ),
      child: child,
    );
  }
}

class _Crest extends StatelessWidget {
  const _Crest(this.name, {required this.home, this.radius = 16});
  final String name;
  final bool home;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return CircleAvatar(
      radius: radius,
      backgroundColor: home ? scheme.primary : scheme.secondary,
      child: Text(_mark(name), style: TextStyle(color: home ? Colors.white : scheme.onSecondary, fontSize: radius * 0.62, fontWeight: FontWeight.w800)),
    );
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill(this.value);
  final String value;

  @override
  Widget build(BuildContext context) {
    final live = value == 'in_progress';
    final done = value == 'completed' || value == 'corrected';
    final bg = live ? const Color(0xFF166534) : done ? const Color(0xFFE7E5E4) : const Color(0xFFFEF3C7);
    final fg = live ? Colors.white : done ? const Color(0xFF44403C) : const Color(0xFF92400E);
    final label = live ? 'Live' : _words(value);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(20)),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(live ? Icons.circle : done ? Icons.flag : Icons.schedule, size: 10, color: fg),
          const SizedBox(width: 4),
          Text(label, style: TextStyle(color: fg, fontSize: 12, fontWeight: FontWeight.w800)),
        ],
      ),
    );
  }
}

class _Col extends StatelessWidget {
  const _Col(this.text, this.width, {this.strong = false});
  final String text;
  final double width;
  final bool strong;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: width,
      child: Text(text, textAlign: TextAlign.end, style: TextStyle(fontSize: 13, fontWeight: strong ? FontWeight.w800 : FontWeight.w600)),
    );
  }
}

class _StaffChip extends StatelessWidget {
  const _StaffChip(this.role, this.name);
  final String role;
  final Object? name;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final value = '${name ?? ''}'.trim();
    final label = value.isEmpty || value.toLowerCase() == role.toLowerCase() ? role : '$role · $value';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(color: scheme.primary.withValues(alpha: 0.08), borderRadius: BorderRadius.circular(20)),
      child: Text(label, style: TextStyle(color: scheme.primary, fontSize: 12, fontWeight: FontWeight.w700)),
    );
  }
}

String _mark(String name) {
  final parts = name.split(RegExp(r'[^A-Za-z0-9]+')).where((part) => part.isNotEmpty).toList();
  if (parts.length >= 2 && parts.last.length <= 2) return parts.last.toUpperCase();
  if (parts.length >= 2) return '${parts.first[0]}${parts.last[0]}'.toUpperCase();
  if (parts.isEmpty) return '?';
  return parts.first[0].toUpperCase();
}

String _pretty(String value) {
  final parts = value.split(RegExp(r'[-_\s]+')).where((part) => part.isNotEmpty).toList();
  if (parts.isEmpty) return value;
  return parts.map((part) => '${part[0].toUpperCase()}${part.substring(1)}').join(' ');
}

String _money(Object? minor) {
  if (minor is! num) return '$minor';
  final amount = minor / 100;
  return amount == amount.roundToDouble() ? amount.toStringAsFixed(0) : amount.toStringAsFixed(2);
}

String _playerLine(dynamic player) {
  final position = _words('${player['position']}');
  final role = '${player['role'] ?? ''}'.trim();
  if (role.isEmpty || role == 'null' || role == 'unassigned') return position;
  return '$position · ${_words(role)}';
}

String _when(String? value) {
  if (value == null || value.isEmpty) return '';
  final text = value.replaceFirst('T', ' ').replaceFirst('Z', '');
  return text.length >= 16 ? text.substring(0, 16) : text;
}

String _eventTitle(dynamic event) {
  final type = '${event['type']}';
  if (type == 'match_start') return 'Match started';
  if (type == 'match_end') return 'Full time';
  if (type == 'compensation') return 'Correction';
  if (type == 'score') return _words('${event['payload']?['event_type'] ?? 'Score'}');
  return _words(type);
}

String _eventDetail(dynamic event) {
  final payload = event['payload'];
  if (payload is! Map || payload.isEmpty) return '';
  final bits = <String>[];
  if (payload['team_id'] != null) bits.add('${payload['team_id']}');
  if (payload['reason'] != null) bits.add('${payload['reason']}');
  return bits.join(' · ');
}

String _words(String value) {
  if (value == 'null' || value.isEmpty) return '';
  return value.replaceAll('_', ' ');
}

String _plain(String? code) {
  switch (code) {
    case 'SQUAD_FULL':
      return 'the squad is full';
    case 'TRANSFER_WINDOW_CLOSED':
      return 'the transfer window is closed';
    case 'OUT_OF_STOCK':
      return 'that item is sold out';
    case 'MATCH_NOT_IN_PROGRESS':
      return 'the match has not started';
    default:
      return (code ?? 'the server kept its current result').replaceAll('_', ' ').toLowerCase();
  }
}
