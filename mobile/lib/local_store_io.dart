import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';

class LocalStore {
  Database? _db;

  Future<Database> get db async {
    if (_db != null) return _db!;
    final folder = await getDatabasesPath();
    _db = await openDatabase(
      p.join(folder, 'nascon.db'),
      version: 1,
      onCreate: (database, version) async {
        await database.execute('CREATE TABLE cache(key TEXT PRIMARY KEY, body TEXT NOT NULL)');
        await database.execute(
          'CREATE TABLE queue(id INTEGER PRIMARY KEY AUTOINCREMENT, action_id TEXT, body TEXT NOT NULL, label TEXT NOT NULL, result TEXT)',
        );
      },
    );
    return _db!;
  }

  Future<void> save(String key, Object value) async {
    final database = await db;
    await database.insert('cache', {'key': key, 'body': jsonEncode(value)}, conflictAlgorithm: ConflictAlgorithm.replace);
  }

  Future<Map<String, dynamic>?> readMap(String key) async {
    final database = await db;
    final rows = await database.query('cache', where: 'key = ?', whereArgs: [key]);
    if (rows.isEmpty) return null;
    return jsonDecode(rows.first['body'] as String) as Map<String, dynamic>;
  }

  Future<void> enqueue(Map<String, dynamic> action, String label) async {
    final database = await db;
    await database.insert('queue', {
      'action_id': action['action_id'],
      'body': jsonEncode(action),
      'label': label,
    });
  }

  Future<List<Map<String, dynamic>>> pending() async {
    final database = await db;
    final rows = await database.query('queue', where: 'result IS NULL', orderBy: 'id');
    return rows
        .map((row) => {
              'id': row['id'],
              'label': row['label'],
              'action': jsonDecode(row['body'] as String),
            })
        .toList();
  }

  Future<void> mark(int id, String result) async {
    final database = await db;
    await database.update('queue', {'result': result}, where: 'id = ?', whereArgs: [id]);
  }

  Future<void> clearDone() async {
    final database = await db;
    await database.delete('queue', where: 'result IS NOT NULL');
  }

  Future<String> savePdf(String name, String base64Pdf) async {
    final folder = await getDatabasesPath();
    final file = File(p.join(folder, '$name.pdf'));
    await file.writeAsBytes(base64Decode(base64Pdf), flush: true);
    return file.path;
  }
}
