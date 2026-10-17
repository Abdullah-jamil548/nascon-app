import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
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
  ThemeData theme = _theme(const Color(0xFF0B3D2E), const Color(0xFFF5C518));

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'NaSCon League',
      debugShowCheckedModeBanner: false,
      theme: theme,
      home: RootPage(
        api: api,
        onTenant: (primary, secondary) {
          setState(() => theme = _theme(primary, secondary));
        },
      ),
    );
  }
}

ThemeData _theme(Color primary, Color secondary) {
  final scheme = ColorScheme.fromSeed(seedColor: primary, primary: primary, secondary: secondary, brightness: Brightness.light);
  return ThemeData(
    colorScheme: scheme,
    useMaterial3: true,
    textTheme: const TextTheme(bodyLarge: TextStyle(fontSize: 16), titleLarge: TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(minimumSize: const Size(48, 48), textStyle: const TextStyle(fontSize: 16)),
    ),
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
  String link = 'Online';

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    base = _definedBase.isNotEmpty ? _definedBase : (prefs.getString('base') ?? base);
    token = await storage.read(key: 'token');
    role = await storage.read(key: 'role') ?? '';
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
        throw ApiException(message.toString());
      }
      link = 'Online';
      return decoded;
    } catch (error) {
      if (error is ApiException) rethrow;
      link = 'Offline';
      throw ApiException('You are offline. Saved screens are still available.');
    }
  }

  Future<void> login(String email, String password) async {
    final data = await send('POST', '/api/v1/auth/login', {'email': email, 'password': password});
    token = data['token'] as String;
    role = data['role'] as String;
    await storage.write(key: 'token', value: token);
    await storage.write(key: 'role', value: role);
    final tenant = data['tenant'];
    if (tenant is Map) {
      await local.save('tenant', Map<String, dynamic>.from(tenant));
    }
  }

  Future<void> logout() async {
    token = null;
    role = '';
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

  @override
  void initState() {
    super.initState();
    _boot();
  }

  Future<void> _boot() async {
    await widget.api.load();
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
  bool busy = false;

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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Sign in')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          const Text('League operations', style: TextStyle(fontSize: 28, fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          const Text('Players, fixtures, standings and the club store.'),
          const SizedBox(height: 20),
          TextField(controller: email, decoration: const InputDecoration(labelText: 'Email'), keyboardType: TextInputType.emailAddress),
          const SizedBox(height: 12),
          TextField(controller: password, decoration: const InputDecoration(labelText: 'Password'), obscureText: true),
          const SizedBox(height: 12),
          TextField(controller: server, decoration: const InputDecoration(labelText: 'Server address', helperText: 'Emulator: http://10.0.2.2:8080  Phone: http://laptop-ip:8080')),
          if (error != null) Padding(padding: const EdgeInsets.only(top: 12), child: Text(error!, style: TextStyle(color: Theme.of(context).colorScheme.error))),
          const SizedBox(height: 20),
          FilledButton(onPressed: busy ? null : submit, child: Text(busy ? 'Signing in…' : 'Sign in')),
        ],
      ),
    );
  }
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

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _loadQueue();
    _sync(automatic: true);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
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
    if (rows.isEmpty) {
      setState(() => widget.api.link = widget.api.link == 'Offline' ? 'Offline' : 'Online');
      return;
    }
    setState(() => widget.api.link = 'Syncing');
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
      }
      await widget.api.local.clearDone();
      widget.api.link = 'Online';
    } on ApiException {
      widget.api.link = 'Offline';
    }
    await _loadQueue();
    if (!automatic && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Sync finished')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final pages = [
      FixturesPage(api: widget.api, onQueued: _loadQueue),
      StandingsPage(api: widget.api),
      StorePage(api: widget.api),
      ClubPage(api: widget.api, onQueued: _loadQueue),
    ];
    return Scaffold(
      appBar: AppBar(
        title: const Text('NaSCon League'),
        actions: [
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Chip(
              label: Text(widget.api.link),
              avatar: Icon(_linkIcon(widget.api.link), size: 18),
            ),
          ),
          IconButton(tooltip: 'Sync now', onPressed: () => _sync(), icon: const Icon(Icons.sync)),
          IconButton(
            tooltip: 'Sign out',
            onPressed: () async {
              await widget.api.logout();
              widget.onLogout();
            },
            icon: const Icon(Icons.logout),
          ),
        ],
      ),
      body: Column(
        children: [
          if (queue.isNotEmpty)
            Material(
              color: Theme.of(context).colorScheme.secondaryContainer,
              child: ListTile(
                title: Text('${queue.length} pending action${queue.length == 1 ? '' : 's'}'),
                subtitle: Text(queue.map((row) => row['label']).join(', ')),
              ),
            ),
          Expanded(child: pages[tab]),
        ],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: tab,
        onDestinationSelected: (value) => setState(() => tab = value),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.sports_soccer), label: 'Fixtures'),
          NavigationDestination(icon: Icon(Icons.leaderboard), label: 'Standings'),
          NavigationDestination(icon: Icon(Icons.storefront), label: 'Store'),
          NavigationDestination(icon: Icon(Icons.shield), label: 'Club'),
        ],
      ),
    );
  }
}

class FixturesPage extends StatefulWidget {
  const FixturesPage({super.key, required this.api, required this.onQueued});
  final Api api;
  final Future<void> Function() onQueued;

  @override
  State<FixturesPage> createState() => _FixturesPageState();
}

class _FixturesPageState extends State<FixturesPage> {
  List<dynamic> rows = [];
  String? error;
  bool loading = true;

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
    if (loading) return const Center(child: CircularProgressIndicator());
    if (rows.isEmpty) return const Center(child: Text('No matches yet'));
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView.separated(
        padding: const EdgeInsets.all(12),
        itemCount: rows.length,
        separatorBuilder: (_, index) => const SizedBox(height: 8),
        itemBuilder: (context, index) {
          final match = Map<String, dynamic>.from(rows[index] as Map);
          final score = Map<String, dynamic>.from(match['score'] as Map? ?? {});
          return Card(
            child: ListTile(
              minTileHeight: 64,
              title: Text('${match['home']} vs ${match['away']}'),
              subtitle: Text('${match['status']}  ${match['starts_at'] ?? ''}'),
              trailing: Text('${score['home'] ?? 0}–${score['away'] ?? 0}', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
              onTap: () => Navigator.push(context, MaterialPageRoute(builder: (_) => MatchPage(api: widget.api, matchId: match['match_id'] as String, onQueued: widget.onQueued))),
            ),
          );
        },
      ),
    );
  }
}

class MatchPage extends StatefulWidget {
  const MatchPage({super.key, required this.api, required this.matchId, required this.onQueued});
  final Api api;
  final String matchId;
  final Future<void> Function() onQueued;

  @override
  State<MatchPage> createState() => _MatchPageState();
}

class _MatchPageState extends State<MatchPage> {
  Map<String, dynamic>? match;
  String? message;

  @override
  void initState() {
    super.initState();
    _load();
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

  Future<void> _score(String eventType) async {
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
        'payload': {'team_id': match?['home']?['team_id'], 'event_type': eventType},
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
    return Scaffold(
      appBar: AppBar(title: Text('${home['team_id']} vs ${away['team_id']}')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('${home['score']} – ${away['score']}', textAlign: TextAlign.center, style: const TextStyle(fontSize: 48, fontWeight: FontWeight.w800)),
          Text('${match!['status']} · ${match!['outcome']}', textAlign: TextAlign.center),
          if (match!['pinned_variant_id'] != null)
            Card(child: ListTile(leading: const Icon(Icons.push_pin), title: const Text('Pinned product'), subtitle: Text('${match!['pinned_variant_id']}'))),
          const SizedBox(height: 12),
          const Text('Timeline', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          if (events.isEmpty) const Text('No events yet'),
          for (final event in events)
            ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text('${event['type']}'),
              subtitle: Text('${event['payload']}'),
              trailing: event['compensated'] == true ? const Text('Corrected') : null,
            ),
          if (message != null) Text(message!),
          if (organizer) ...[
            const SizedBox(height: 8),
            FilledButton(onPressed: () => _score('goal'), child: const Text('Add goal for home')),
            const SizedBox(height: 8),
            OutlinedButton(onPressed: _compensate, child: const Text('Correct last score')),
          ],
        ],
      ),
    );
  }
}

class StandingsPage extends StatefulWidget {
  const StandingsPage({super.key, required this.api});
  final Api api;

  @override
  State<StandingsPage> createState() => _StandingsPageState();
}

class _StandingsPageState extends State<StandingsPage> {
  List<dynamic> table = [];
  bool loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await widget.api.send('GET', '/api/v1/standings/demo-league');
      table = data['table'] as List? ?? [];
      await widget.api.local.save('standings', data);
    } on ApiException {
      final cached = await widget.api.local.readMap('standings');
      table = cached?['table'] as List? ?? [];
    }
    if (mounted) setState(() => loading = false);
  }

  @override
  Widget build(BuildContext context) {
    if (loading) return const Center(child: CircularProgressIndicator());
    if (table.isEmpty) return const Center(child: Text('No matches yet'));
    return ListView.separated(
      padding: const EdgeInsets.all(12),
      itemCount: table.length,
      separatorBuilder: (_, index) => const Divider(height: 1),
      itemBuilder: (context, index) {
        final row = Map<String, dynamic>.from(table[index] as Map);
        return ListTile(
          minTileHeight: 56,
          leading: CircleAvatar(child: Text('${row['rank']}')),
          title: Text('${row['team_id']}'),
          subtitle: Text('${row['points']} pts · ${row['status']}'),
          trailing: Text('${row['won']}-${row['drawn']}-${row['lost']}'),
        );
      },
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
  String? receipt;
  String? message;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await widget.api.send('GET', '/api/v1/products');
      products = data['products'] as List? ?? [];
    } on ApiException catch (caught) {
      message = caught.message;
    }
    if (mounted) setState(() {});
  }

  Future<void> buy(String variantId) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Buy this item?'),
        content: const Text('The mock gateway will take payment.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Buy')),
        ],
      ),
    );
    if (confirm != true) return;
    try {
      final data = await widget.api.send('POST', '/api/v1/checkout', {
        'order_id': 'ord-${DateTime.now().millisecondsSinceEpoch}',
        'customer_id': 'phone-user',
        'items': [
          {'variant_id': variantId, 'quantity': 1}
        ],
        'discount_code': 'WELCOME10',
        'payment': {'token': 'tok_success'},
      });
      final payload = data['receipt']['payload'];
      setState(() {
        receipt = '${payload['order_id']}  PKR ${(payload['total_minor'] as int) / 100}';
        message = 'Payment captured. Receipt is ready.';
      });
    } on ApiException catch (caught) {
      setState(() => message = caught.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (products.isEmpty) return Center(child: Text(message ?? 'No products yet'));
    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        if (message != null) Text(message!),
        if (receipt != null) Card(child: ListTile(title: const Text('Receipt'), subtitle: Text(receipt!))),
        for (final product in products)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(product['name'] as String, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
                  Text(product['category'] as String),
                  for (final variant in (product['variants'] as List))
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Row(
                        children: [
                          Expanded(child: Text('${variant['size']} · stock ${variant['stock']}')),
                          FilledButton(onPressed: () => buy(variant['variant_id'] as String), child: const Text('Buy')),
                        ],
                      ),
                    ),
                ],
              ),
            ),
          ),
      ],
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
    if (mounted) setState(() {});
  }

  Future<void> _transfer() async {
    final body = {
      'request_id': 'tr-${DateTime.now().millisecondsSinceEpoch}',
      'league_id': 'demo-roster',
      'player_id': 'demo-df',
      'type': 'transfer',
      'from_squad_id': 'squad-alpha-1',
      'to_squad_id': 'squad-alpha-2',
      'effective_date': '2026-10-17',
      'loan_end_date': null,
      'reason': 'Matchday rotation',
      'requested_by': 'manager',
    };
    final action = {
      'action_id': body['request_id'],
      'type': 'roster_transfer',
      'queued_at': '${DateTime.now().toUtc().toIso8601String().split('.').first}Z',
      'base_version': 0,
      'payload': body,
    };
    if (widget.api.link == 'Offline') {
      await widget.api.local.enqueue(action, 'Transfer Demo Back');
      await widget.onQueued();
      setState(() => message = 'Transfer saved on this phone.');
      return;
    }
    try {
      await widget.api.send('POST', '/api/v1/roster/transfer', body);
      setState(() => message = 'Demo Back moved to Alpha Reserves.');
      await _load();
    } on ApiException catch (caught) {
      await widget.api.local.enqueue(action, 'Transfer Demo Back');
      await widget.onQueued();
      setState(() => message = caught.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (club == null) return Center(child: Text(message ?? 'Loading club…'));
    final staff = Map<String, dynamic>.from(club!['staff'] as Map? ?? {});
    final venue = Map<String, dynamic>.from(club!['venue'] as Map? ?? {});
    final roster = (club!['roster'] as List? ?? []);
    final manager = widget.api.role == 'club_manager' || widget.api.role == 'super_admin';
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text(club!['name'] as String, style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w800)),
        Text('Home: ${venue['name']} (${venue['latitude']}, ${venue['longitude']})'),
        const SizedBox(height: 8),
        Text('Coach ${staff['coach']} · Manager ${staff['manager']} · Physio ${staff['physio']}'),
        const SizedBox(height: 12),
        const Text('Roster', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
        if (roster.isEmpty) const Text('No players yet'),
        for (final player in roster)
          ListTile(
            contentPadding: EdgeInsets.zero,
            minTileHeight: 56,
            title: Text('${player['name']}'),
            subtitle: Text('${player['position']} · ${player['role'] ?? 'unassigned'}'),
          ),
        if (message != null) Text(message!),
        if (manager) ...[
          const SizedBox(height: 8),
          FilledButton(onPressed: _transfer, child: const Text('Transfer Demo Back')),
          const SizedBox(height: 8),
          OutlinedButton(onPressed: () => _loan(), child: const Text('Loan Demo Back')),
        ],
      ],
    );
  }

  Future<void> _loan() async {
    try {
      await widget.api.send('POST', '/api/v1/roster/transfer', {
        'request_id': 'loan-${DateTime.now().millisecondsSinceEpoch}',
        'league_id': 'demo-roster',
        'player_id': 'demo-mf',
        'type': 'loan',
        'from_squad_id': 'squad-alpha-2',
        'to_squad_id': 'squad-alpha-1',
        'effective_date': '2026-10-17',
        'loan_end_date': '2026-11-17',
        'reason': 'Cover injury',
        'requested_by': 'manager',
      });
      setState(() => message = 'Loan recorded until 17 Nov 2026.');
    } on ApiException catch (caught) {
      setState(() => message = caught.message);
    }
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
