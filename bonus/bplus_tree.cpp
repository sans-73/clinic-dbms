// bplus_tree.cpp - B+ tree simulator (C++17). Keys = patient display codes, values = patient names.
// Build:  g++ -std=c++17 bonus/bplus_tree.cpp -o bplus && ./bplus
// ORDER = max children per internal node. A node holds at most ORDER-1 keys; a full node splits.
// Leaves are linked, so a RANGE SCAN walks sideways without going back to the root (that is what makes B+ trees good for BETWEEN / ORDER BY).
#include <algorithm>
#include <iostream>
#include <memory>
#include <queue>
#include <string>
#include <vector>
using namespace std;

constexpr int ORDER = 4;                       // try 5 or 6 and compare the heights

struct Node {
    bool leaf = true;
    vector<string> keys;
    vector<string> vals;                       // leaf only
    vector<Node*> kids;                        // internal only
    Node* next = nullptr;                      // leaf chain
};

struct Split { string up; Node* right = nullptr; };   // result of splitting a child

class BPlusTree {
    Node* root = new Node();
    int splits = 0;

    Split insert(Node* n, const string& k, const string& v) {
        if (n->leaf) {
            auto it = lower_bound(n->keys.begin(), n->keys.end(), k);
            size_t pos = it - n->keys.begin();
            if (it != n->keys.end() && *it == k) { n->vals[pos] = v; return {}; }       // update
            n->keys.insert(it, k);
            n->vals.insert(n->vals.begin() + pos, v);
            if ((int)n->keys.size() < ORDER) return {};
            // split leaf: right half moves to a new leaf, its first key is COPIED up
            Node* r = new Node();
            size_t mid = n->keys.size() / 2;
            r->keys.assign(n->keys.begin() + mid, n->keys.end());
            r->vals.assign(n->vals.begin() + mid, n->vals.end());
            n->keys.resize(mid); n->vals.resize(mid);
            r->next = n->next; n->next = r;
            ++splits;
            return {r->keys.front(), r};
        }
        size_t i = upper_bound(n->keys.begin(), n->keys.end(), k) - n->keys.begin();
        Split s = insert(n->kids[i], k, v);
        if (!s.right) return {};
        n->keys.insert(n->keys.begin() + i, s.up);
        n->kids.insert(n->kids.begin() + i + 1, s.right);
        if ((int)n->keys.size() < ORDER) return {};
        // split internal node: middle key MOVES up
        Node* r = new Node(); r->leaf = false;
        size_t mid = n->keys.size() / 2;
        string up = n->keys[mid];
        r->keys.assign(n->keys.begin() + mid + 1, n->keys.end());
        r->kids.assign(n->kids.begin() + mid + 1, n->kids.end());
        n->keys.resize(mid); n->kids.resize(mid + 1);
        ++splits;
        return {up, r};
    }

public:
    void insert(const string& k, const string& v) {
        Split s = insert(root, k, v);
        if (s.right) {                                     // root split: tree grows one level
            Node* nr = new Node(); nr->leaf = false;
            nr->keys = {s.up}; nr->kids = {root, s.right};
            root = nr;
        }
    }
    // point search; counts how many nodes (= page reads) were visited
    const string* search(const string& k, int& reads) const {
        Node* n = root; reads = 1;
        while (!n->leaf) { n = n->kids[upper_bound(n->keys.begin(), n->keys.end(), k) - n->keys.begin()]; ++reads; }
        auto it = lower_bound(n->keys.begin(), n->keys.end(), k);
        if (it != n->keys.end() && *it == k) return &n->vals[it - n->keys.begin()];
        return nullptr;
    }
    void range(const string& lo, const string& hi) const {
        Node* n = root;
        while (!n->leaf) n = n->kids[upper_bound(n->keys.begin(), n->keys.end(), lo) - n->keys.begin()];
        int leaves = 0;
        for (; n; n = n->next) {
            ++leaves;
            for (size_t i = 0; i < n->keys.size(); ++i) {
                if (n->keys[i] > hi) { cout << "  (scanned " << leaves << " leaf page(s) via the leaf chain)\n"; return; }
                if (n->keys[i] >= lo) cout << "  " << n->keys[i] << "  " << n->vals[i] << "\n";
            }
        }
        cout << "  (scanned " << leaves << " leaf page(s))\n";
    }
    void print() const {
        queue<pair<Node*, int>> q; q.push({root, 0});
        int level = -1;
        while (!q.empty()) {
            auto [n, d] = q.front(); q.pop();
            if (d != level) { level = d; cout << (d == 0 ? "" : "\n") << "level " << d << (n->leaf ? " (leaves)" : "") << ": "; }
            cout << "[";
            for (size_t i = 0; i < n->keys.size(); ++i) cout << (i ? " | " : "") << n->keys[i];
            cout << "] ";
            for (Node* c : n->kids) q.push({c, d + 1});
        }
        cout << "\nnode splits so far: " << splits << "\n";
    }
    int height() const { int h = 1; for (Node* n = root; !n->leaf; n = n->kids[0]) ++h; return h; }
};

int main() {
    BPlusTree t;
    const vector<pair<string, string>> patients = {
        {"CLN1-000105", "Ramesh Sharma"}, {"CLN1-000112", "Priya Verma"}, {"CLN1-000101", "Mohammad Irfan"},
        {"CLN1-000120", "Sunita Devi"},   {"CLN1-000108", "Anil Kumar"},   {"CLN1-000131", "Gurpreet Singh"},
        {"CLN1-000117", "Meena Patel"},   {"CLN1-000103", "Kavita Joshi"}, {"CLN1-000125", "Imran Khan"},
        {"CLN1-000110", "Neha Gupta"},    {"CLN1-000128", "Rohit Yadav"},  {"CLN1-000115", "Asha Jain"},
        {"CLN1-000122", "Vijay Soni"},    {"CLN1-000106", "Farzana Khan"}, {"CLN1-000134", "Dinesh Dubey"}};
    cout << "== inserting patient codes, ORDER = " << ORDER << " ==\n";
    for (auto& [k, v] : patients) {
        t.insert(k, v);
        cout << "\ninsert " << k << "\n";
        t.print();
    }
    cout << "\n== point search (reads = nodes visited root->leaf; tree height = " << t.height() << ") ==\n";
    for (string k : {"CLN1-000117", "CLN1-000999"}) {
        int reads; const string* v = t.search(k, reads);
        cout << k << " -> " << (v ? *v : "not found") << "   page reads: " << reads << "\n";
    }
    cout << "\n== range scan  CLN1-000108 <= code <= CLN1-000120  (SQL: WHERE display_code BETWEEN ...) ==\n";
    t.range("CLN1-000108", "CLN1-000120");
}
