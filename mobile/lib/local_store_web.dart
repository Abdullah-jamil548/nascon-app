import 'dart:convert';

class LocalStore {
  final Map<String, String> _cache = {};
  final List<Map<String, dynamic>> _queue = [];
  var _nextId = 1;

  Future<void> save(String key, Object value) async {
    _cache[key] = jsonEncode(value);
  }

  Future<Map<String, dynamic>?> readMap(String key) async {
    final body = _cache[key];
    if (body == null) return null;
    return jsonDecode(body) as Map<String, dynamic>;
  }

  Future<void> enqueue(Map<String, dynamic> action, String label) async {
    _queue.add({'id': _nextId++, 'action_id': action['action_id'], 'body': jsonEncode(action), 'label': label});
  }

  Future<List<Map<String, dynamic>>> pending() async {
    return _queue
        .where((row) => row['result'] == null)
        .map((row) => {
              'id': row['id'],
              'label': row['label'],
              'action': jsonDecode(row['body'] as String),
            })
        .toList();
  }

  Future<void> mark(int id, String result) async {
    for (final row in _queue) {
      if (row['id'] == id) row['result'] = result;
    }
  }

  Future<void> clearDone() async {
    _queue.removeWhere((row) => row['result'] != null);
  }

  Future<String> savePdf(String name, String base64Pdf) async {
    return 'Open the phone app to save $name.pdf.';
  }
}
