import Contacts
import Foundation

// contacts-bridge: minimal, stable Contacts I/O. No sync logic lives here —
// keep this binary small and unchanging so its one-time TCC grant never
// needs to be re-earned. All merge/matching logic lives in the Python
// orchestrator that calls this as a subprocess.

func requestAccessSync() -> Bool {
    let store = CNContactStore()
    let status = CNContactStore.authorizationStatus(for: .contacts)
    if status == .authorized { return true }
    let sema = DispatchSemaphore(value: 0)
    var granted = false
    store.requestAccess(for: .contacts) { ok, _ in
        granted = ok
        sema.signal()
    }
    sema.wait()
    return granted
}

struct ContactOut: Codable {
    let id: String
    let givenName: String
    let familyName: String
    let organization: String
    let phones: [String: String]   // label -> value, first value per label
    let emails: [String: String]
}

func dumpContacts() {
    guard requestAccessSync() else {
        FileHandle.standardError.write("ERROR: Contacts access not granted\n".data(using: .utf8)!)
        exit(1)
    }
    let store = CNContactStore()
    let keys: [CNKeyDescriptor] = [
        CNContactIdentifierKey as CNKeyDescriptor,
        CNContactGivenNameKey as CNKeyDescriptor,
        CNContactFamilyNameKey as CNKeyDescriptor,
        CNContactOrganizationNameKey as CNKeyDescriptor,
        CNContactPhoneNumbersKey as CNKeyDescriptor,
        CNContactEmailAddressesKey as CNKeyDescriptor
    ]
    let request = CNContactFetchRequest(keysToFetch: keys)
    var out: [ContactOut] = []
    try? store.enumerateContacts(with: request) { contact, _ in
        var phones: [String: String] = [:]
        for p in contact.phoneNumbers {
            let label = p.label.map { CNLabeledValue<CNPhoneNumber>.localizedString(forLabel: $0) } ?? "other"
            if phones[label] == nil { phones[label] = p.value.stringValue }
        }
        var emails: [String: String] = [:]
        for e in contact.emailAddresses {
            let label = e.label.map { CNLabeledValue<NSString>.localizedString(forLabel: $0) } ?? "other"
            if emails[label] == nil { emails[label] = e.value as String }
        }
        out.append(ContactOut(
            id: contact.identifier,
            givenName: contact.givenName,
            familyName: contact.familyName,
            organization: contact.organizationName,
            phones: phones,
            emails: emails
        ))
    }
    let enc = JSONEncoder()
    enc.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let data = try? enc.encode(out) {
        FileHandle.standardOutput.write(data)
    }
}

struct ContactUpdate: Codable {
    let id: String?          // nil = create new
    let givenName: String?
    let familyName: String?
    let setPhones: [String: String]?   // label -> value, only fields to set
    let setEmails: [String: String]?
}

struct ApplyResult: Codable {
    let id: String
    let created: Bool
    let ok: Bool
    let error: String?
}

func applyUpdates(from path: String) {
    guard requestAccessSync() else {
        FileHandle.standardError.write("ERROR: Contacts access not granted\n".data(using: .utf8)!)
        exit(1)
    }
    guard let data = FileManager.default.contents(atPath: path),
          let updates = try? JSONDecoder().decode([ContactUpdate].self, from: data) else {
        FileHandle.standardError.write("ERROR: could not read/parse \(path)\n".data(using: .utf8)!)
        exit(1)
    }
    let store = CNContactStore()
    var results: [ApplyResult] = []
    for u in updates {
        let saveRequest = CNSaveRequest()
        if let id = u.id, !id.isEmpty {
            let keys: [CNKeyDescriptor] = [
                CNContactGivenNameKey as CNKeyDescriptor,
                CNContactFamilyNameKey as CNKeyDescriptor,
                CNContactPhoneNumbersKey as CNKeyDescriptor,
                CNContactEmailAddressesKey as CNKeyDescriptor
            ]
            guard let existing = try? store.unifiedContact(withIdentifier: id, keysToFetch: keys),
                  let mutable = existing.mutableCopy() as? CNMutableContact else {
                results.append(ApplyResult(id: id, created: false, ok: false, error: "not found"))
                continue
            }
            var phones = mutable.phoneNumbers
            for (label, value) in (u.setPhones ?? [:]) {
                phones.removeAll { $0.label == label }
                phones.append(CNLabeledValue(label: label, value: CNPhoneNumber(stringValue: value)))
            }
            mutable.phoneNumbers = phones
            var emails = mutable.emailAddresses
            for (label, value) in (u.setEmails ?? [:]) {
                emails.removeAll { $0.label == label }
                emails.append(CNLabeledValue(label: label, value: value as NSString))
            }
            mutable.emailAddresses = emails
            saveRequest.update(mutable)
            do {
                try store.execute(saveRequest)
                results.append(ApplyResult(id: id, created: false, ok: true, error: nil))
            } catch {
                results.append(ApplyResult(id: id, created: false, ok: false, error: "\(error)"))
            }
        } else {
            let mutable = CNMutableContact()
            mutable.givenName = u.givenName ?? ""
            mutable.familyName = u.familyName ?? ""
            for (label, value) in (u.setPhones ?? [:]) {
                mutable.phoneNumbers.append(CNLabeledValue(label: label, value: CNPhoneNumber(stringValue: value)))
            }
            for (label, value) in (u.setEmails ?? [:]) {
                mutable.emailAddresses.append(CNLabeledValue(label: label, value: value as NSString))
            }
            saveRequest.add(mutable, toContainerWithIdentifier: nil)
            do {
                try store.execute(saveRequest)
                results.append(ApplyResult(id: mutable.identifier, created: true, ok: true, error: nil))
            } catch {
                results.append(ApplyResult(id: "", created: true, ok: false, error: "\(error)"))
            }
        }
    }
    let enc = JSONEncoder()
    enc.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let outData = try? enc.encode(results) {
        FileHandle.standardOutput.write(outData)
    }
}

func deleteContacts(ids: [String]) {
    guard requestAccessSync() else {
        FileHandle.standardError.write("ERROR: Contacts access not granted\n".data(using: .utf8)!)
        exit(1)
    }
    let store = CNContactStore()
    let keys: [CNKeyDescriptor] = [CNContactIdentifierKey as CNKeyDescriptor]
    var results: [ApplyResult] = []
    for id in ids {
        let saveRequest = CNSaveRequest()
        guard let existing = try? store.unifiedContact(withIdentifier: id, keysToFetch: keys),
              let mutable = existing.mutableCopy() as? CNMutableContact else {
            results.append(ApplyResult(id: id, created: false, ok: false, error: "not found"))
            continue
        }
        saveRequest.delete(mutable)
        do {
            try store.execute(saveRequest)
            results.append(ApplyResult(id: id, created: false, ok: true, error: nil))
        } catch {
            results.append(ApplyResult(id: id, created: false, ok: false, error: "\(error)"))
        }
    }
    let enc = JSONEncoder()
    enc.outputFormatting = [.prettyPrinted, .sortedKeys]
    if let outData = try? enc.encode(results) {
        FileHandle.standardOutput.write(outData)
    }
}

let args = CommandLine.arguments
if args.count >= 2 && args[1] == "dump" {
    dumpContacts()
} else if args.count >= 3 && args[1] == "apply" {
    applyUpdates(from: args[2])
} else if args.count >= 3 && args[1] == "delete" {
    deleteContacts(ids: Array(args[2...]))
} else if args.count >= 2 && args[1] == "check-auth" {
    let status = CNContactStore.authorizationStatus(for: .contacts)
    print(status.rawValue)
} else {
    FileHandle.standardError.write("usage: contacts-bridge dump | apply <updates.json> | delete <id> [id...] | check-auth\n".data(using: .utf8)!)
    exit(1)
}
